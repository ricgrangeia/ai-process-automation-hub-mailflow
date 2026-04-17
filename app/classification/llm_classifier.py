import json
import logging
import re
import time
import httpx
from .contracts import ClassificationResult
from app.core.i18n import t

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

logger = logging.getLogger("llm-classifier")


class LLMClassifier:

    def __init__(self, settings):
        self.settings = settings

    async def classify(self, email, rule_hint: str | None = None, folders: list[str] | None = None):
        """
        Classify an email.

        rule_hint: if provided, a learned rule has already suggested this folder.
                   The LLM is asked to validate the suggestion rather than classify
                   from scratch — it should agree (high confidence) or flag a conflict.
        """

        folder_list = ", ".join(folders) if folders else "Invoices, Work, Personal, Marketing, Spam, Other"

        hint_block = t("prompt.classifier.rule_hint", rule_hint=rule_hint) if rule_hint else ""

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

            # 🔴 Handle non-200
            if response.status_code != 200:
                logger.error(f"LLM returned {response.status_code}: {response.text}")
                return ClassificationResult("NeedsReview", 0.0)

            # 🔴 Handle invalid JSON body
            try:
                data = response.json()
            except Exception:
                logger.error(f"Invalid JSON response from LLM: {response.text}")
                return ClassificationResult("NeedsReview", 0.0)

            # 🔴 Validate structure
            if "choices" not in data or not data["choices"]:
                logger.error(f"Malformed LLM response: {data}")
                return ClassificationResult("NeedsReview", 0.0)

            content = data["choices"][0]["message"]["content"]

            if not content:
                logger.error("Empty content from LLM")
                return ClassificationResult("NeedsReview", 0.0)

            # 🔥 Extract JSON safely (even if model adds text)
            try:
                start = content.find("{")
                end = content.rfind("}") + 1
                json_str = content[start:end]
                parsed = json.loads(json_str)
            except Exception:
                logger.error(f"Failed to parse LLM JSON content: {content}")
                return ClassificationResult("NeedsReview", 0.0)

            folder = parsed.get("folder", "NeedsReview")
            confidence = parsed.get("confidence", 0.0)
            sender_type = parsed.get("sender_type")
            sender_name = parsed.get("sender_name")

            try:
                confidence = float(confidence)
            except Exception:
                confidence = 0.0

            # Clamp confidence
            confidence = max(0.0, min(confidence, 1.0))

            # Normalise sender fields
            if sender_type not in ("company", "person"):
                sender_type = None
            if sender_name in (None, "null", "NULL", ""):
                sender_name = None

            result = ClassificationResult(folder, confidence)
            result.sender_type = sender_type
            result.sender_name = sender_name
            result.llm_time_seconds = round(_llm_time, 3)
            return result

        except httpx.RequestError as e:
            logger.error(f"LLM request failed: {type(e).__name__}: {e}")
            return ClassificationResult("NeedsReview", 0.0)

        except Exception as e:
            logger.error(f"Unexpected LLM error: {type(e).__name__}: {e}")
            return ClassificationResult("NeedsReview", 0.0)
