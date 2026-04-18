"""
Invoice service — single entry point for extracting and persisting invoice data.

Both the ai-worker and invoice-worker call these functions.
Neither worker imports extract_qr_from_pdf, persist_invoice, or
extract_financial_from_body directly.

Public API
----------
save_invoice_from_pdf(session_factory, email_id, pdf_path, settings)
    → dict | None

save_invoice_from_body(session_factory, email_id, subject, body_text, settings, language)
    → dict | None
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger("invoices.service")


async def _enrich_seller_name(invoice_data: dict, settings) -> None:
    """
    If seller_name is missing, call the ai-api NIF lookup tool and patch
    invoice_data in place. Fire-and-forget — errors never propagate.
    """
    if invoice_data.get("seller_name"):
        return
    nif = invoice_data.get("nif_seller")
    if not nif:
        return

    db_url          = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    tool_server_url = getattr(settings, "tool_server_url", None)
    api_key         = getattr(settings, "tool_server_api_key", "") or ""

    if not db_url or not tool_server_url:
        return

    try:
        from app.invoices.nif_lookup import resolve_seller_name
        name = await resolve_seller_name(nif, db_url, tool_server_url, api_key)
        if name:
            invoice_data["seller_name"] = name
    except Exception as e:
        logger.warning(f"NIF enrichment failed for {nif}: {e}")


async def extract_invoice_from_pdf(
    pdf_path: str | Path,
    settings,
) -> dict | None:
    """
    Extract invoice data from a PDF via the tool server QR endpoint.
    Does NOT persist anything to the DB — use this for pre-checks (e.g. ATCUD gate).

    Returns the raw invoice dict, or None if extraction fails / produces no data.
    """
    from app.invoices.extractor import extract_qr_from_pdf

    if not getattr(settings, "tool_server_url", None):
        logger.debug("tool_server_url not configured — skipping PDF extraction")
        return None

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        logger.warning(f"PDF not found: {pdf_path}")
        return None

    try:
        results = await extract_qr_from_pdf(
            str(pdf_path),
            settings.tool_server_url,
            getattr(settings, "tool_server_api_key", "") or "",
        )
    except Exception as e:
        logger.warning(f"PDF extraction error ({pdf_path.name}): {e}")
        return None

    return results[0] if results else None


async def save_invoice_from_pdf(
    session_factory,
    email_id: int,
    pdf_path: str | Path,
    settings,
) -> dict | None:
    """
    Extract invoice data from a PDF via the tool server QR endpoint,
    persist to DB, and return the invoice dict.

    Returns None if extraction fails or produces no data.
    """
    from app.invoices.extractor import persist_invoice

    invoice_data = await extract_invoice_from_pdf(pdf_path, settings)
    if not invoice_data:
        return None

    try:
        await persist_invoice(session_factory, email_id, invoice_data)
        logger.info(f"Invoice persisted from PDF — email_id={email_id}, file={Path(pdf_path).name}")
    except Exception as e:
        logger.error(f"persist_invoice failed for email {email_id}: {e}")
        return None

    await _enrich_seller_name(invoice_data, settings)
    return invoice_data


async def save_invoice_from_body(
    session_factory,
    email_id: int,
    subject: str,
    body_text: str,
    settings,
    language: str = "en",
) -> dict | None:
    """
    Extract financial data from an email body via the LLM,
    persist to DB, and return the invoice dict.

    Returns None if extraction fails or the LLM returns nothing useful.
    """
    from app.invoices.extractor import persist_invoice
    from app.invoice_worker.body_extractor import extract_financial_from_body

    try:
        result = await extract_financial_from_body(
            subject=subject,
            body_text=body_text,
            llm_base_url=settings.llm_base_url,
            llm_api_key=getattr(settings, "llm_api_key", "") or "",
            llm_model=settings.llm_model,
            language=language,
        )
    except Exception as e:
        logger.warning(f"Body extraction error for email {email_id}: {e}")
        return None

    if not result:
        return None

    result["invoice_origin"] = result.get("invoice_origin") or "payment_confirmation"

    try:
        await persist_invoice(session_factory, email_id, result)
        logger.info(
            f"Invoice persisted from body — email_id={email_id}, "
            f"origin={result['invoice_origin']}"
        )
    except Exception as e:
        logger.error(f"persist_invoice failed for email {email_id}: {e}")
        return None

    await _enrich_seller_name(result, settings)
    return result
