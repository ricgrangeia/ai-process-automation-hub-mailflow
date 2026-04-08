"""
Invoice QR extractor — reads PDF attachments, calls the AI Tool Server
/tools/pdf/qr/decode-base64 endpoint, parses the result, persists to DB.
"""

import base64
import logging
from pathlib import Path

import httpx

logger = logging.getLogger("invoice.extractor")


async def extract_qr_from_pdf(pdf_path: str, tool_server_url: str, api_key: str = "") -> list[dict]:
    """
    Calls /tools/pdf/invoice/decode-base64 — the combined endpoint that runs
    QR decode + payment text extraction in parallel and merges with the LLM.

    Falls back to an empty list on any error.
    """
    path = Path(pdf_path)
    if not path.exists():
        logger.warning(f"PDF not found: {pdf_path}")
        return []

    try:
        pdf_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read PDF {pdf_path}: {e}")
        return []

    headers = {"x-api-key": api_key} if api_key else {}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{tool_server_url}/tools/pdf/invoice/decode-base64",
                json={"filename": path.name, "file_base64": pdf_b64},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"Invoice extraction failed for {pdf_path}: {e}")
        return []

    invoice = data.get("invoice", {})
    if not invoice or not any(invoice.values()):
        logger.info(f"No invoice data found in {path.name}")
        return []

    logger.info(f"Invoice extracted from {path.name}: {list(k for k, v in invoice.items() if v)}")
    return [invoice]


async def persist_invoice(session_factory, email_id: int, data: dict) -> None:
    """
    Upsert an Invoice row.

    Deduplication priority:
      1. (nif_seller, invoice_number) — business key; same invoice number from the
         same seller in the same year is always the same document.
      2. email_id fallback — for partial records where QR was not decoded.

    Update policy:
      - Never overwrite fields that already have a value.
      - Always fill in NULL fields, including MB payment data discovered later.
    """
    from sqlalchemy import select
    from app.invoices.models import Invoice

    nif_seller     = data.get("nif_seller")
    invoice_number = data.get("invoice_number")

    async with session_factory() as session:
        existing = None

        # 1 — match by business key
        if nif_seller and invoice_number:
            existing = (await session.execute(
                select(Invoice).where(
                    Invoice.nif_seller == nif_seller,
                    Invoice.invoice_number == invoice_number,
                )
            )).scalar_one_or_none()

        # 2 — fallback: same email
        if existing is None:
            existing = (await session.execute(
                select(Invoice).where(Invoice.email_id == email_id)
            )).scalar_one_or_none()

        if existing:
            filled = [
                k for k, v in data.items()
                if hasattr(existing, k) and v is not None and getattr(existing, k) is None
            ]
            for k in filled:
                setattr(existing, k, data[k])
            if filled:
                await session.commit()
                logger.info(f"Invoice {existing.id} updated — filled: {filled}")
            else:
                logger.info(f"Invoice {existing.id} already complete, nothing to update")
        else:
            session.add(Invoice(email_id=email_id, **{
                k: v for k, v in data.items() if hasattr(Invoice, k)
            }))
            await session.commit()
            logger.info(f"Invoice created for email {email_id} ({invoice_number or 'no QR'})")
