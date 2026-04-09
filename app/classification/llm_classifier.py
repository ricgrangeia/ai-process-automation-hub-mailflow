import json
import logging
import time
import httpx
from .contracts import ClassificationResult

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

        if rule_hint:
            hint_block = (
                f"\nA learned rule — based on a previous human-confirmed decision for "
                f"this sender — suggests this email belongs to: **{rule_hint}**\n"
                f"Validate this carefully:\n"
                f"- If the email content matches what you would expect for \"{rule_hint}\", "
                f"return \"{rule_hint}\" with confidence ≥ 0.90.\n"
                f"- If the content seems different from what the rule expects "
                f"(e.g. same sender domain but this email is a newsletter, not an invoice), "
                f"return the correct folder independently with your honest confidence.\n"
            )
        else:
            hint_block = ""

        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a strict email classifier. Return ONLY valid JSON."
                },
                {
                    "role": "user",
                    "content": f"""
Classify into one of:
{folder_list}
{hint_block}
Also identify the sender:
- sender_type: "company" if sent by a business/organisation, "person" if sent by an individual
- sender_name: the company name (e.g. "Amazon", "LinkedIn") or person's name (e.g. "João Silva") — NOT the email address

Return exactly:
{{
  "folder": "FolderName",
  "confidence": 0.0-1.0,
  "sender_type": "company" or "person",
  "sender_name": "Name or null"
}}

From: {email.from_address}
Subject: {email.subject}
Body:
{(email.body_text or "")[:1500]}
"""
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
