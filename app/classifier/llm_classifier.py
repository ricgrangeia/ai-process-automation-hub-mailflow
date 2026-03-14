import json
import logging
import httpx
from .base import ClassificationResult

logger = logging.getLogger("llm-classifier")


class LLMClassifier:

    def __init__(self, settings):
        self.settings = settings

    async def classify(self, email):

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
Invoices, Work, Personal, Marketing, Spam, Other

Return exactly:
{{
  "folder": "FolderName",
  "confidence": 0.0-1.0
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
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url}/chat/completions",
                    json=payload,
                    headers={
                        "x-api-key": self.settings.llm_api_key
                    }
                )

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

            try:
                confidence = float(confidence)
            except Exception:
                confidence = 0.0

            # Clamp confidence
            confidence = max(0.0, min(confidence, 1.0))

            return ClassificationResult(folder, confidence)

        except httpx.RequestError as e:
            logger.error(f"LLM request failed: {e}")
            return ClassificationResult("NeedsReview", 0.0)

        except Exception as e:
            logger.error(f"Unexpected LLM error: {e}")
            return ClassificationResult("NeedsReview", 0.0)