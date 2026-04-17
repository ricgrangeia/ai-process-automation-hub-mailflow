"""
LLM-based financial data extractor for email bodies.

Called only when keyword detection says the email looks financial
but there is no PDF attachment. Uses the configured LLM to extract
structured data from the plain-text body.

Returns a dict compatible with the Invoice model (same field names).
"""

import json
import logging
import re
from pathlib import Path

import httpx

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

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
        body=_URL_RE.sub("[url]", body_text or "(empty)"),
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

    result = _infer_payment_status(subject, body_text, result)
    logger.info(f"Body extraction result: { {k: v for k, v in result.items() if v is not None} }")
    return result


def _infer_payment_status(subject: str, body_text: str, result: dict) -> dict:
    """
    Rule-based post-processor that validates and corrects invoice_origin.

    Small LLMs frequently confuse paid vs unpaid documents, especially in
    Portuguese. This function uses unambiguous keyword signals to override
    a likely-wrong origin value.

    Rules:
      - fatura_recibo signals  → origin = "fatura_recibo"   (paid)
      - recibo/receipt signals → origin = "receipt"          (paid)
      - payment-confirmed      → origin = "payment_confirmation" (paid)
      - unpaid signals + LLM
        said it was paid       → origin = "pt_at_invoice"   (not paid)
    """
    text = ((subject or "") + " " + (body_text or "")).lower()

    # ── Paid signals ─────────────────────────────────────────────────────────

    # Strongest signal: explicit fatura-recibo document
    if any(s in text for s in ("fatura-recibo", "fatura recibo")):
        if result.get("invoice_origin") != "fatura_recibo":
            logger.info("payment_status_override: fatura-recibo detected → fatura_recibo")
            result["invoice_origin"] = "fatura_recibo"
        return result

    confirmed_paid_phrases = [
        "pagamento confirmado", "pagamento efetuado", "pagamento efectuado",
        "pagamento realizado", "pagamento recebido", "pagamento concluído",
        "payment confirmed", "payment received", "payment successful",
        "comprovativo de pagamento",
    ]
    if any(p in text for p in confirmed_paid_phrases):
        if result.get("invoice_origin") not in ("payment_confirmation", "bank_transfer"):
            logger.info("payment_status_override: payment-confirmed phrase → payment_confirmation")
            result["invoice_origin"] = "payment_confirmation"
        return result

    receipt_phrases = [
        "recibo de pagamento", "recibo emitido", "receipt issued",
    ]
    has_receipt_word = "recibo" in text or "receipt" in text
    if any(p in text for p in receipt_phrases) or (
        has_receipt_word
        and not any(x in text for x in ("fatura", "invoice"))  # standalone recibo only
    ):
        if result.get("invoice_origin") not in ("receipt", "fatura_recibo", "payment_confirmation"):
            logger.info("payment_status_override: recibo/receipt signal → receipt")
            result["invoice_origin"] = "receipt"
        return result

    transfer_confirmed = [
        "transferência efetuada", "transferência efectuada", "transferência concluída",
        "wire transfer sent", "bank transfer confirmed",
    ]
    if any(p in text for p in transfer_confirmed):
        if result.get("invoice_origin") != "bank_transfer":
            logger.info("payment_status_override: transfer-confirmed phrase → bank_transfer")
            result["invoice_origin"] = "bank_transfer"
        return result

    # ── Unpaid signals ────────────────────────────────────────────────────────
    # If these appear AND the LLM classified as a paid type, correct it.
    unpaid_phrases = [
        "para pagamento", "referência para pagamento", "referencia para pagamento",
        "prazo de pagamento", "data limite de pagamento", "data limite pagamento",
        "aguarda pagamento", "aguardamos o seu pagamento",
        "proceda ao pagamento", "efectue o pagamento", "efetue o pagamento",
        "due date", "payment due", "amount due",
    ]
    _paid_origins = {"receipt", "fatura_recibo", "payment_confirmation", "bank_transfer"}
    if any(p in text for p in unpaid_phrases):
        if result.get("invoice_origin") in _paid_origins:
            logger.info(
                "payment_status_override: unpaid signal found but LLM said '%s' → pt_at_invoice",
                result.get("invoice_origin"),
            )
            result["invoice_origin"] = "pt_at_invoice"

    return result
