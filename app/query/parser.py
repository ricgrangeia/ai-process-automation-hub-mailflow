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

_SYSTEM_PROMPT = """You extract email search filters and return ONLY a JSON object. No explanation, no text, just JSON.

JSON fields: sender_domain, sender_email, folder, date_from, date_to, keyword.
folder must be one of: Invoices, Work, Personal, Marketing, Spam, Other, or null.
Dates format: YYYY-MM-DD.

Examples:
Request: invoices from amazon.com January 2026
{"sender_domain":"amazon.com","sender_email":null,"folder":"Invoices","date_from":"2026-01-01","date_to":"2026-01-31","keyword":null}

Request: marketing emails this year (today=2026-04-05)
{"sender_domain":null,"sender_email":null,"folder":"Marketing","date_from":"2026-01-01","date_to":"2026-12-31","keyword":null}

Request: how many work emails last month (today=2026-04-05)
{"sender_domain":null,"sender_email":null,"folder":"Work","date_from":"2026-03-01","date_to":"2026-03-31","keyword":null}

Request: emails from john@company.com with keyword meeting
{"sender_domain":null,"sender_email":"john@company.com","folder":null,"date_from":null,"date_to":null,"keyword":"meeting"}

Request: spam this month (today=2026-04-05)
{"sender_domain":null,"sender_email":null,"folder":"Spam","date_from":"2026-04-01","date_to":"2026-04-30","keyword":null}"""


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

        logger.info(f"LLM raw response: {content!r}")

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
