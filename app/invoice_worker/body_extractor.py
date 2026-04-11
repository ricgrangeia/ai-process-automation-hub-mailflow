"""
LLM-based financial data extractor for email bodies.

Called only when keyword detection says the email looks financial
but there is no PDF attachment. Uses the configured LLM to extract
structured data from the plain-text body.

Returns a dict compatible with the Invoice model (same field names).
"""

import json
import logging
from pathlib import Path

import httpx

logger = logging.getLogger("invoice_worker.body_extractor")

_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(name: str, language: str = "en") -> str:
    key = f"{language}/{name}"
    if key not in _PROMPT_CACHE:
        import os
        lang = language if language in ("en", "pt") else "en"
        root = Path(__file__).resolve().parent.parent.parent / "locales" / lang
        path = root / name
        if not path.exists():
            path = Path(__file__).resolve().parent.parent.parent / "locales" / "en" / name
        _PROMPT_CACHE[key] = path.read_text(encoding="utf-8")
    return _PROMPT_CACHE[key]


async def extract_financial_from_body(
    subject: str,
    body_text: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    language: str = "en",
) -> dict | None:
    """
    Ask the LLM to extract financial fields from the email body.

    Returns a dict with Invoice-compatible fields, or None on failure.
    invoice_origin will be set by the worker based on LLM output.
    """
    if not llm_base_url or not llm_model:
        logger.warning("LLM not configured — skipping body extraction")
        return None

    prompt_template = _load_prompt("prompt.invoice.body.txt", language)
    prompt = prompt_template.format(
        subject=subject or "(no subject)",
        body=body_text or "(empty)",
    )

    headers = {"Content-Type": "application/json"}
    if llm_api_key:
        headers["Authorization"] = f"Bearer {llm_api_key}"

    payload = {
        "model": llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 512,
    }

    try:
        base = llm_base_url.rstrip("/").removesuffix("/v1")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"LLM body extraction failed: {e}")
        return None

    try:
        raw = data["choices"][0]["message"]["content"]
        # Extract JSON block if wrapped in markdown fences
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
    except Exception as e:
        logger.error(f"Failed to parse LLM body extraction response: {e}\nRaw: {data}")
        return None

    if not isinstance(result, dict):
        logger.warning(f"LLM body extraction returned non-dict: {result}")
        return None

    logger.info(f"Body extraction result: { {k: v for k, v in result.items() if v is not None} }")
    return result
