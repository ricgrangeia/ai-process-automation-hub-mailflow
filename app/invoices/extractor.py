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

    # Response may be a list of QR strings or a dict with a "qr_codes" key
    qr_strings: list[str] = []
    if isinstance(data, list):
        qr_strings = [str(x) for x in data if x]
    elif isinstance(data, dict):
        codes = data.get("qr_codes") or data.get("results") or []
        qr_strings = [str(x) for x in codes if x]

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

    logger.info(f"Extracted {len(results)} invoice QR(s) from {path.name}")
    return results


async def persist_invoice(session_factory, email_id: int, data: dict) -> None:
    """Upsert an Invoice row for the given email."""
    from sqlalchemy import select
    from app.invoices.models import Invoice

    async with session_factory() as session:
        existing = (await session.execute(
            select(Invoice).where(Invoice.email_id == email_id)
        )).scalar_one_or_none()

        if existing:
            for k, v in data.items():
                if hasattr(existing, k) and v is not None:
                    setattr(existing, k, v)
        else:
            session.add(Invoice(email_id=email_id, **{
                k: v for k, v in data.items() if hasattr(Invoice, k)
            }))

        await session.commit()
    logger.info(f"Invoice record saved for email {email_id}")
