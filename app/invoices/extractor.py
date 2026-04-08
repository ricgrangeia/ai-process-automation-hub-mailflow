"""
Invoice QR extractor — reads PDF attachments, calls the AI Tool Server
/tools/pdf/qr/decode-base64 endpoint, parses the result, persists to DB.
"""

import base64
import logging
from pathlib import Path

import httpx

from app.invoices.qr_parser import parse_pt_invoice_qr

logger = logging.getLogger("invoice.extractor")


async def extract_qr_from_pdf(pdf_path: str, tool_server_url: str, api_key: str = "") -> list[dict]:
    """
    Sends a PDF to the Tool Server QR decode endpoint.
    Returns a list of parsed invoice dicts (one per unique QR found).
    """
    path = Path(pdf_path)
    if not path.exists():
        logger.warning(f"PDF not found: {pdf_path}")
        return []

    try:
        pdf_bytes = path.read_bytes()
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read PDF {pdf_path}: {e}")
        return []

    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{tool_server_url}/tools/pdf/qr/decode-base64",
                json={"filename": path.name, "file_base64": pdf_b64},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"Tool Server QR decode failed for {pdf_path}: {e}")
        return []

    # Response: {"qrcodes": [{"data": "...", "method": "...", ...}, ...], ...}
    qr_strings: list[str] = []
    if isinstance(data, list):
        # Flat list of strings or dicts
        for x in data:
            if isinstance(x, dict):
                v = x.get("data")
                if v:
                    qr_strings.append(str(v))
            elif x:
                qr_strings.append(str(x))
    elif isinstance(data, dict):
        codes = data.get("qrcodes") or data.get("qr_codes") or data.get("results") or []
        for x in codes:
            if isinstance(x, dict):
                v = x.get("data")
                if v:
                    qr_strings.append(str(v))
            elif x:
                qr_strings.append(str(x))

    if not qr_strings:
        logger.info(f"No QR codes found in {pdf_path}")
        return []

    results = []
    for raw in qr_strings:
        parsed = parse_pt_invoice_qr(raw)
        if parsed.get("nif_seller") or parsed.get("invoice_number"):
            results.append(parsed)
        else:
            logger.debug(f"QR not recognised as PT invoice: {raw[:80]}")

    # --- Also extract Multibanco payment data via text layer ---
    mb = await extract_mb_payment_from_pdf(pdf_path, tool_server_url, api_key)
    if mb:
        for r in results:
            r.update(mb)
        if not results:
            # No QR but we have MB data — return it as a partial record
            results.append(mb)

    logger.info(f"Extracted {len(results)} invoice QR(s) from {path.name}")
    return results


async def extract_mb_payment_from_pdf(pdf_path: str, tool_server_url: str, api_key: str = "") -> dict:
    """
    Calls /tools/pdf/payment/decode-base64 to extract Multibanco payment fields.
    Returns a dict with mb_* keys, or empty dict on failure/no data.
    """
    path = Path(pdf_path)
    try:
        pdf_bytes = path.read_bytes()
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read PDF {pdf_path}: {e}")
        return {}

    headers = {"x-api-key": api_key} if api_key else {}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{tool_server_url}/tools/pdf/payment/decode-base64",
                json={"filename": path.name, "file_base64": pdf_b64},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Payment extraction failed for {pdf_path}: {e}")
        return {}

    mb = data.get("mb_payment", {})
    if not any(mb.values()):
        return {}

    # LLM endpoint already returns mb_* prefixed keys; map directly to model fields
    result = {}
    for field in ("mb_entidade", "mb_referencia", "mb_data_limite"):
        if mb.get(field):
            result[field] = mb[field]
    if mb.get("mb_valor") is not None:
        try:
            result["mb_valor"] = float(mb["mb_valor"])
        except (ValueError, TypeError):
            pass

    logger.info(f"MB payment extracted from {path.name}: {result}")
    return result


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
