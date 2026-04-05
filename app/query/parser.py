"""
QueryParser — uses the LLM to extract structured filters from a natural language message.

Input:  "send me all invoices from amazon.com January 2026"
Output: {
    "sender_domain": "amazon.com",
    "folder": "Invoices",
    "date_from": "2026-01-01",
    "date_to": "2026-01-31",
    "keyword": null
}

Any field not mentioned is returned as null and treated as "no filter".
"""

import json
import logging
import httpx
from datetime import date

logger = logging.getLogger("query.parser")

_SYSTEM_PROMPT = """
You are a JSON-only email filter extractor. Your ONLY job is to output a single JSON object. Never explain, never answer questions, never add text outside the JSON.

Output exactly this structure:
{"sender_domain":null,"sender_email":null,"folder":null,"date_from":null,"date_to":null,"keyword":null}

Rules:
- folder must be one of: Invoices, Work, Personal, Marketing, Spam, Other — or null.
- "marketing" or "newsletters" → folder=Marketing.
- "invoices" or "faturas" → folder=Invoices.
- "this year" → date_from=CURRENT_YEAR-01-01, date_to=CURRENT_YEAR-12-31.
- "last month" → compute first and last day of the previous month.
- "this month" → compute first and last day of the current month.
- "how many", "count", "show me", "list", "find" → ignore these words and extract the filters from the rest.
- If a field is not mentioned, set it to null.
- Output ONLY the JSON object. No explanation. No markdown. No extra text.
"""


async def parse_query(user_message: str, settings) -> dict:
    """
    Calls the LLM to extract search filters from a natural language query.
    Returns a dict with keys: sender_domain, sender_email, folder,
    date_from, date_to, keyword. Any unknown field is None.
    """
    today = date.today().isoformat()

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Today is {today}. Extract email search filters as JSON from this request: {user_message}"},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{settings.llm_base_url}/chat/completions",
                json=payload,
                headers={"x-api-key": settings.llm_api_key},
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]

        start = content.find("{")
        end = content.rfind("}") + 1
        if start == -1 or end == 0:
            logger.error(f"LLM returned no JSON object. Raw content: {content!r}")
            return None

        raw_json = content[start:end]

        try:
            filters = json.loads(raw_json)
        except json.JSONDecodeError:
            # Some models return single-quoted or unquoted JSON — try ast.literal_eval as fallback
            import ast
            try:
                filters = ast.literal_eval(raw_json)
            except Exception:
                logger.error(f"LLM returned unparseable JSON. Raw: {raw_json!r}")
                return None

        # Normalise null strings to None
        def _null(v):
            return None if v in (None, "null", "NULL", "") else v

        return {
            "sender_domain": _null(filters.get("sender_domain")),
            "sender_email":  _null(filters.get("sender_email")),
            "folder":        _null(filters.get("folder")),
            "date_from":     _null(filters.get("date_from")),
            "date_to":       _null(filters.get("date_to")),
            "keyword":       _null(filters.get("keyword")),
        }

    except Exception as e:
        logger.error(f"Query parsing failed: {type(e).__name__}: {e}")
        return None
