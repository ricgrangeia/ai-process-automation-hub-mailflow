"""
Invoice QR extractor — reads PDF attachments, calls the AI Tool Server
/tools/pdf/qr/decode-base64 endpoint, parses the result, persists to DB.
"""

import base64
import logging
from datetime import datetime
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

    logger.info(f"Tool server response status: {resp.status_code}")
    logger.debug(f"Tool server raw response: {data}")

    invoices = data.get("invoices", [])
    if not invoices:
        logger.warning(f"No invoice data found in {path.name} — raw response keys: {list(data.keys())}")
        return []

    results = []
    for invoice in invoices:
        if not invoice or not any(invoice.values()):
            continue
        populated = {k: v for k, v in invoice.items() if v is not None}
        logger.info(f"Invoice extracted from {path.name}: {populated}")

        # document_type_description is derived from document_type — no LLM needed
        doc_type = invoice.get("document_type")
        if doc_type and not invoice.get("document_type_description"):
            from app.invoices.document_types import DOCUMENT_TYPES
            invoice["document_type_description"] = DOCUMENT_TYPES.get(doc_type)

        results.append(invoice)

    return results


PAYMENT_FIELDS = {
    "payment_method", "mb_entidade", "mb_referencia", "mb_valor",
    "mb_data_limite", "iban", "mbway_phone",
}


async def persist_invoice(session_factory, email_id: int, data: dict) -> None:
    """
    Upsert an Invoice row.

    Deduplication priority:
      1. (nif_seller, invoice_number) — business key; same invoice number from the
         same seller in the same year is always the same document.
      2. email_id fallback — for partial records where QR was not decoded.

    Update policy:
      - Identity/QR fields (nif, amounts, dates): fill NULL only, never overwrite.
      - Payment fields (mb_*, iban, mbway_phone): always overwrite with new non-null
        value — allows re-processing the same PDF to fix missing payment data.
    """
    from sqlalchemy import select
    from app.invoices.models import Invoice

    # Coerce invoice_date string → datetime.date (SQLAlchemy DateTime rejects strings)
    if isinstance(data.get("invoice_date"), str):
        try:
            data["invoice_date"] = datetime.strptime(data["invoice_date"], "%Y-%m-%d").date()
        except ValueError:
            data["invoice_date"] = None

    nif_seller     = data.get("nif_seller")
    invoice_number = data.get("invoice_number")

    logger.info(
        f"persist_invoice called — email_id={email_id}, "
        f"nif_seller={nif_seller!r}, invoice_number={invoice_number!r}"
    )
    logger.info(f"Incoming data fields: { {k: v for k, v in data.items() if v is not None} }")

    atcud = data.get("atcud")

    async with session_factory() as session:
        existing = None

        # 1 — match by business key (nif_seller + invoice_number)
        if nif_seller and invoice_number:
            existing = (await session.execute(
                select(Invoice).where(
                    Invoice.nif_seller == nif_seller,
                    Invoice.invoice_number == invoice_number,
                )
            )).scalar_one_or_none()
            if existing:
                logger.info(
                    f"Matched existing invoice id={existing.id} by business key "
                    f"(nif_seller={nif_seller!r}, invoice_number={invoice_number!r}), "
                    f"original email_id={existing.email_id}"
                )
            else:
                logger.info(f"No existing invoice found by business key ({nif_seller!r}, {invoice_number!r})")
        else:
            logger.info("Business key incomplete — skipping business-key lookup")

        # 1b — match by ATCUD (unique PT invoice identifier)
        if existing is None and atcud:
            existing = (await session.execute(
                select(Invoice).where(Invoice.atcud == atcud)
            )).scalar_one_or_none()
            if existing:
                logger.info(f"Matched existing invoice id={existing.id} by atcud={atcud!r}")
            else:
                logger.info(f"No existing invoice found by atcud={atcud!r}")

        # 2 — fallback: same email
        if existing is None:
            existing = (await session.execute(
                select(Invoice).where(Invoice.email_id == email_id)
            )).scalar_one_or_none()
            if existing:
                logger.info(f"Matched existing invoice id={existing.id} by email_id={email_id}")
            else:
                logger.info(f"No existing invoice found for email_id={email_id} — will create new")

        if existing:
            filled   = []
            updated  = []
            skipped  = []

            for k, v in data.items():
                if not hasattr(existing, k) or v is None:
                    continue
                current = getattr(existing, k)
                if k in PAYMENT_FIELDS:
                    if current != v:
                        setattr(existing, k, v)
                        updated.append(f"{k}: {current!r} → {v!r}")
                    else:
                        skipped.append(f"{k} (same value)")
                else:
                    if current is None:
                        setattr(existing, k, v)
                        filled.append(f"{k}={v!r}")
                    else:
                        skipped.append(f"{k} (already set: {current!r})")

            if filled or updated:
                await session.commit()
                if filled:
                    logger.info(f"Invoice {existing.id} — filled null fields: {filled}")
                if updated:
                    logger.info(f"Invoice {existing.id} — updated payment fields: {updated}")
            else:
                logger.info(f"Invoice {existing.id} — no changes needed")
            if skipped:
                logger.info(f"Invoice {existing.id} — skipped (already set or same): {skipped}")
        else:
            new_inv = Invoice(email_id=email_id, **{
                k: v for k, v in data.items() if hasattr(Invoice, k)
            })
            session.add(new_inv)
            await session.commit()
            logger.info(f"Invoice created for email {email_id} ({invoice_number or 'no QR'})")
