import json
import logging
import re
import time
import httpx
from .contracts import ClassificationResult
from app.core.i18n import t

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

logger = logging.getLogger("llm-classifier")


def _build_context_block(context: dict) -> str:
    """
    Format the classification context dict into a prompt block.

    context = {
        "sender_history": [{"folder": "Faturas", "hits": 15}, ...],
        "matched_keywords": ["fatura", "pagamento"],
    }

    Returns an empty string when context carries no useful information.
    """
    lines = []

    history = context.get("sender_history") or []
    keywords = context.get("matched_keywords") or []

    if history:
        lines.append("Past confirmed classifications for this sender:")
        for entry in history:
            times = entry["hits"]
            label = "time" if times == 1 else "times"
            lines.append(f"  - {entry['folder']}: confirmed {times} {label}")

    if keywords:
        lines.append(f"Financial keywords present in this email: {', '.join(keywords)}")

    if not lines:
        return ""

    lines.append(
        "\nUse this history as supporting context — it reflects past human decisions. "
        "If the current email content is clearly different from past patterns "
        "(e.g. a newsletter from a sender that normally sends invoices), "
        "classify it correctly regardless of history."
    )

    return "\n" + "\n".join(lines) + "\n"


class LLMClassifier:

    def __init__(self, settings):
        self.settings = settings

    async def classify(
        self,
        email,
        context: dict | None = None,
        folders: list[str] | None = None,
        # kept for backward compat (rules_only mode passes rule_hint)
        rule_hint: str | None = None,
    ):
        folder_list = ", ".join(folders) if folders else "Invoices, Work, Personal, Marketing, Spam, Other"

        # Context block: rich sender history + keywords (new default path)
        if context is not None:
            hint_block = _build_context_block(context)
        elif rule_hint:
            # Legacy path — rules_only mode or direct calls with rule_hint
            hint_block = t("prompt.classifier.rule_hint", rule_hint=rule_hint)
        else:
            hint_block = ""

        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": t("prompt.classifier.system"),
                },
                {
                    "role": "user",
                    "content": t(
                        "prompt.classifier.user",
                        folder_list=folder_list,
                        hint_block=hint_block,
                        from_address=email.from_address,
                        subject=email.subject,
                        body=_URL_RE.sub("[url]", (email.body_text or "")[:1500]),
                    ),
                }
            ],
            "temperature": 0.0
        }

        try:
            _t0 = time.perf_counter()
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.settings.llm_api_key}"
                    }
                )
            _llm_time = time.perf_counter() - _t0

            if response.status_code != 200:
                logger.error(f"LLM returned {response.status_code}: {response.text}")
                return ClassificationResult("NeedsReview", 0.0)

            try:
                data = response.json()
            except Exception:
                logger.error(f"Invalid JSON response from LLM: {response.text}")
                return ClassificationResult("NeedsReview", 0.0)

            if "choices" not in data or not data["choices"]:
                logger.error(f"Malformed LLM response: {data}")
                return ClassificationResult("NeedsReview", 0.0)

            content = data["choices"][0]["message"]["content"]

            if not content:
                logger.error("Empty content from LLM")
                return ClassificationResult("NeedsReview", 0.0)

            try:
                start = content.find("{")
                end = content.rfind("}") + 1
                json_str = content[start:end]
                parsed = json.loads(json_str)
            except Exception:
                logger.error(f"Failed to parse LLM JSON content: {content}")
                return ClassificationResult("NeedsReview", 0.0)

            folder     = parsed.get("folder", "NeedsReview")
            confidence = parsed.get("confidence", 0.0)
            sender_type = parsed.get("sender_type")
            sender_name = parsed.get("sender_name")

            try:
                confidence = float(confidence)
            except Exception:
                confidence = 0.0

            confidence = max(0.0, min(confidence, 1.0))

            if sender_type not in ("company", "person"):
                sender_type = None
            if sender_name in (None, "null", "NULL", ""):
                sender_name = None

            result = ClassificationResult(folder, confidence)
            result.source = "llm"
            result.sender_type = sender_type
            result.sender_name = sender_name
            result.llm_time_seconds = round(_llm_time, 3)

            has_history = bool((context or {}).get("sender_history"))
            has_keywords = bool((context or {}).get("matched_keywords"))
            if has_history or has_keywords:
                logger.info(
                    f"LLM classified with context "
                    f"(history={len((context or {}).get('sender_history', []))} entries, "
                    f"keywords={len((context or {}).get('matched_keywords', []))}) "
                    f"→ {folder} ({int(confidence*100)}%)"
                )
            else:
                logger.info(f"LLM classified (no prior context) → {folder} ({int(confidence*100)}%)")

            return result

        except httpx.RequestError as e:
            logger.error(f"LLM request failed: {type(e).__name__}: {e}")
            return ClassificationResult("NeedsReview", 0.0)

        except Exception as e:
            logger.error(f"Unexpected LLM error: {type(e).__name__}: {e}")
            return ClassificationResult("NeedsReview", 0.0)
