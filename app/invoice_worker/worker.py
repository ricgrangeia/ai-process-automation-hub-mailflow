"""
Invoice Worker — Redis queue consumer for financial email processing.

Behaviour:
  - Listens on mailai:jobs:invoice (populated by the IMAP worker)
  - For each job:
      • classification=pdf_invoice   → extract invoice via tool server → save DB + archive PDF + move email
      • classification=financial_body → LLM body extraction → save DB + move email
  - Non-financial emails are never enqueued here; the IMAP worker pre-filters them
    and leaves them completely untouched (unread, unmoved) on the mailserver.
"""

import asyncio
import json
import logging
from pathlib import Path

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database.engine import make_engine, make_session_factory
from app.core.database.init import init_db
from app.core.crypto import decrypt_secret
from app.accounts.models import EmailAccount
from app.messages.models import EmailMessage, Attachment
from app.invoices.service import save_invoice_from_pdf, save_invoice_from_body
from app.ingestion.imap.client import connect_imap, move_message
from app.processing.queue import INVOICE_QUEUE_KEY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("invoice-worker")

async def _resolve_folder(session_factory, invoice_data: dict) -> str | None:
    """
    Return the target IMAP folder for this invoice using the document_type_routing table.

    - Looks up the document_type code (e.g. "FT", "FR") in the routing table.
    - If the seller NIF matches one of the buyer's own companies → folder_internal.
    - Otherwise → folder_external.
    - Returns None when the document type is not found in the routing table
      (caller should alert the user and wait for them to add the routing rule).
    - Falls back to "Faturas" on DB errors (fail-safe).
    """
    from app.invoices.document_routing import DocumentTypeRouting
    from app.companies.models import Company

    doc_type   = (invoice_data.get("document_type") or "").upper().strip()
    nif_seller = (invoice_data.get("nif_seller") or "").strip()

    if not doc_type:
        return "Faturas"

    try:
        async with session_factory() as session:
            routing = (await session.execute(
                select(DocumentTypeRouting).where(
                    DocumentTypeRouting.code == doc_type,
                    DocumentTypeRouting.active == True,
                )
            )).scalar_one_or_none()

            if not routing:
                return None  # unknown type — caller must alert

            is_internal = False
            if nif_seller:
                is_internal = (await session.execute(
                    select(Company.id).where(
                        Company.nif == nif_seller,
                        Company.active == True,
                    )
                )).scalar_one_or_none() is not None

        folder = routing.folder_internal if is_internal else routing.folder_external
        return folder or "Faturas"

    except Exception as e:
        logger.warning(f"Document type routing lookup failed: {e} — falling back to Faturas")
        return "Faturas"


# ── AT certainty gate ─────────────────────────────────────────────────────────

def _is_confirmed_at(invoice_data: dict) -> bool:
    """Return True only when ATCUD was decoded from the QR code.

    ATCUD (field H) is assigned by the Portuguese Tax Authority and
    is present in every valid PT AT document since 2023. Its presence
    is the single reliable proof that the PDF is a genuine AT invoice.
    """
    return bool((invoice_data.get("atcud") or "").strip())


# Fields that indicate the extraction actually found something real.
_MEANINGFUL_FIELDS = ("nif_seller", "nif_buyer", "seller_name", "invoice_number", "total_amount")


def _has_meaningful_data(invoice_data: dict | None) -> bool:
    """Return True only if at least one key field is non-null/non-empty.

    The LLM sometimes returns a dict full of string "null" values instead of
    Python None when it cannot find any financial data in the email body.
    This guard prevents those ghost extractions from triggering moves and
    Telegram notifications.
    """
    if not invoice_data:
        return False
    for field in _MEANINGFUL_FIELDS:
        val = invoice_data.get(field)
        if val is not None and str(val).strip().lower() not in ("", "null", "none", "n/a"):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Invoice Telegram card (unified — filed notification + approval request)
# ─────────────────────────────────────────────────────────────────────────────

async def _send_invoice_card(
    bot_token: str,
    chat_id: str,
    email_row: EmailMessage,
    invoice_data: dict,
    target_folder: str,
    *,
    needs_approval: bool = False,
    active_folders: list[str] | None = None,
) -> None:
    """
    Single card for all invoice Telegram notifications.

    needs_approval=False  →  ✅ Filed — no buttons (trusted seller, auto-processed)
    needs_approval=True   →  🧾 Approval Required — Approve / Reject + folder grid + New Folder
    """
    if not bot_token or not chat_id:
        return

    header = "🧾 *New Invoice — Approval Required*" if needs_approval else "✅ *Invoice Filed*"

    parts = [header, "", f"📧 {email_row.from_address or '?'}"]
    if email_row.subject:
        parts.append(f"📋 {email_row.subject}")
    parts.append("─────────────────")
    if invoice_data.get("seller_name"):
        parts.append(f"🏪 {invoice_data['seller_name']}")
    if invoice_data.get("nif_seller"):
        parts.append(f"🪪 NIF: `{invoice_data['nif_seller']}`")
    if invoice_data.get("invoice_number"):
        doc_desc = invoice_data.get("document_type_description") or invoice_data.get("document_type", "")
        num_line = f"📄 `{invoice_data['invoice_number']}`"
        if doc_desc:
            num_line += f" ({doc_desc})"
        parts.append(num_line)
    if invoice_data.get("invoice_date"):
        parts.append(f"📅 {invoice_data['invoice_date']}")
    if invoice_data.get("total_amount") is not None:
        currency = invoice_data.get("currency") or "EUR"
        parts.append(f"💶 Total: *{invoice_data['total_amount']} {currency}*")
    if invoice_data.get("atcud"):
        parts.append(f"ATCUD: `{invoice_data['atcud']}`")
    parts.append("─────────────────")
    parts.append(f"📁 → `{target_folder}`")

    payload: dict = {
        "chat_id": chat_id,
        "text": "\n".join(parts),
        "parse_mode": "Markdown",
    }
    if needs_approval:
        keyboard = []
        # Row 1: quick approve to resolved folder + reject
        keyboard.append([
            {"text": f"✅ Approve → {target_folder}", "callback_data": f"inv_approve:{email_row.id}"},
            {"text": "❌ Reject", "callback_data": f"inv_reject:{email_row.id}"},
        ])
        # Folder grid: move to a different folder (excludes current target)
        if active_folders:
            other = [f for f in active_folders if f != target_folder]
            for i in range(0, len(other), 2):
                keyboard.append([
                    {"text": f, "callback_data": f"inv_approve_to:{email_row.id}:{f}"}
                    for f in other[i:i + 2]
                ])
        # New folder button
        keyboard.append([
            {"text": "➕ New Folder", "callback_data": f"inv_folder_new_request:{email_row.id}"}
        ])
        payload["reply_markup"] = {"inline_keyboard": keyboard}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json=payload,
            )
        logger.info(
            f"Sent invoice card for email {email_row.id} "
            f"({'approval required' if needs_approval else 'filed'})"
        )
    except Exception as e:
        logger.warning(f"Telegram invoice card failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# PDF archive helper
# ─────────────────────────────────────────────────────────────────────────────

async def _archive_pdfs(email_row: EmailMessage, settings, invoice_data: dict) -> None:
    import asyncio
    from app.processing.actions.export_pdf import (
        _resolve_dest, _copy_no_duplicate, _lookup_companies,
        _fetch_pdf_attachments,
    )
    from app.core.system_settings import get_setting, FOLDER_STRUCTURE_KEY, FOLDER_STRUCTURE_DEFAULT

    files_root       = getattr(settings, "files_root", "/files")
    fallback_company = getattr(settings, "company_name", "Company")

    import os
    db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    folder_template = await asyncio.to_thread(
        get_setting, db_url, FOLDER_STRUCTURE_KEY
    ) if db_url else FOLDER_STRUCTURE_DEFAULT

    nif_buyer  = invoice_data.get("nif_buyer")
    nif_seller = invoice_data.get("nif_seller")

    matched      = await asyncio.to_thread(_lookup_companies, nif_buyer, nif_seller)
    company_names = [c["name"] for c in matched] if matched else [fallback_company]

    att_files = await asyncio.to_thread(_fetch_pdf_attachments, email_row.id)
    if not att_files and email_row.raw_path:
        att_src = Path(email_row.raw_path).parent / "attachments"
        if att_src.exists():
            att_files = [(p, p.name) for p in att_src.glob("*.pdf")]

    supplier   = (email_row.from_address or "Unknown").split("@")[0]
    received_at = getattr(email_row, "received_at", None)
    category   = invoice_data.get("invoice_origin", "Invoices").replace("_", " ").title()

    for company_name in company_names:
        dest = _resolve_dest(
            files_root, company_name, category, supplier, received_at, folder_template
        )
        dest.mkdir(parents=True, exist_ok=True)
        for att_path, att_name in att_files:
            result = _copy_no_duplicate(att_path, dest, att_name)
            logger.info(f"Archive {att_name} → {dest} ({result})")


# ─────────────────────────────────────────────────────────────────────────────
# Move email to IMAP folder
# ─────────────────────────────────────────────────────────────────────────────

async def _move_email(
    email_row: EmailMessage, acc: EmailAccount, settings, target_folder: str
) -> None:
    password = decrypt_secret(settings.master_key, acc.password_encrypted)

    def _do_move():
        conn = connect_imap(acc.imap_host, acc.imap_port or 993, acc.username, password)
        try:
            move_message(conn, settings.inbox_folder, target_folder, email_row.imap_uid)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    try:
        await asyncio.to_thread(_do_move)
        logger.info(f"Moved email {email_row.id} → {target_folder}")
    except Exception as e:
        logger.error(f"IMAP move failed for email {email_row.id}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Finalize email record
# ─────────────────────────────────────────────────────────────────────────────

async def _finalize_email(
    session_factory,
    email_id: int,
    folder: str,
    invoice_data: dict | None,
) -> None:
    from datetime import datetime, timezone

    sender_name = None
    if invoice_data:
        sender_name = invoice_data.get("seller_name") or invoice_data.get("nif_seller")

    async with session_factory() as session:
        row = await session.get(EmailMessage, email_id)
        if row:
            row.status               = "moved"
            row.classification_label = folder
            row.processed_at         = datetime.now(timezone.utc)
            row.ai_source            = "invoice_worker"
            row.sender_type          = "company"
            if sender_name and not row.sender_name:
                row.sender_name = sender_name
            await session.commit()
            logger.info(
                f"Finalized email {email_id} → {folder} "
                f"(sender: company / {sender_name or '?'})"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Trusted-seller check
# ─────────────────────────────────────────────────────────────────────────────

async def _is_trusted_seller(session_factory, invoice_data: dict) -> bool:
    """Return True if the seller NIF is in the sellers table with trusted=True."""
    nif = (invoice_data.get("nif_seller") or "").strip()
    if not nif:
        return False

    from app.sellers.models import Seller

    async with session_factory() as session:
        seller = (await session.execute(
            select(Seller).where(Seller.nif == nif)
        )).scalar_one_or_none()

    return bool(seller and seller.trusted)


# ─────────────────────────────────────────────────────────────────────────────

async def _send_unknown_doctype_alert(
    bot_token: str,
    chat_id: str,
    email_row: EmailMessage,
    invoice_data: dict,
) -> None:
    """
    Sent when an invoice has a valid ATCUD but its document type code is not
    in the document_type_routing table. Asks the user to add the routing rule
    and then recheck the invoice.
    """
    if not bot_token or not chat_id:
        return

    doc_type = invoice_data.get("document_type") or "?"
    doc_desc = invoice_data.get("document_type_description") or ""

    parts = [
        "⚠️ *Unknown Document Type*",
        "",
        f"An invoice arrived with ATCUD but document type `{doc_type}`"
        + (f" ({doc_desc})" if doc_desc else "")
        + " has no routing rule configured.",
        "",
        f"📧 {email_row.from_address or '?'}",
    ]
    if email_row.subject:
        parts.append(f"📋 {email_row.subject}")
    parts.append("─────────────────")
    if invoice_data.get("seller_name"):
        parts.append(f"🏪 {invoice_data['seller_name']}")
    if invoice_data.get("nif_seller"):
        parts.append(f"🪪 NIF: `{invoice_data['nif_seller']}`")
    if invoice_data.get("invoice_number"):
        parts.append(f"📄 `{invoice_data['invoice_number']}`")
    if invoice_data.get("total_amount") is not None:
        currency = invoice_data.get("currency") or "EUR"
        parts.append(f"💶 Total: *{invoice_data['total_amount']} {currency}*")
    if invoice_data.get("atcud"):
        parts.append(f"ATCUD: `{invoice_data['atcud']}`")
    parts.append("─────────────────")
    parts.append("👉 Add a routing rule for this type in the dashboard, then tap *Recheck*.")

    payload = {
        "chat_id": chat_id,
        "text": "\n".join(parts),
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": [[
            {"text": "🔄 Recheck Invoice", "callback_data": f"inv_recheck:{email_row.id}"},
        ]]},
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json=payload,
            )
        logger.info(f"Sent unknown doc-type alert for email {email_row.id} (type={doc_type})")
    except Exception as e:
        logger.warning(f"Telegram unknown doc-type alert failed: {e}")


async def _set_pending_review(
    session_factory, email_id: int, target_folder: str
) -> None:
    """Mark email as pending_review and store the resolved target folder."""
    from datetime import datetime, timezone
    async with session_factory() as session:
        row = await session.get(EmailMessage, email_id)
        if row:
            row.status = "pending_review"
            row.classification_label = target_folder
            await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Process a single email (loaded from DB by id)
# ─────────────────────────────────────────────────────────────────────────────

async def _process_email_by_id(
    session_factory,
    settings,
    email_id: int,
    classification: str,
) -> None:
    """
    Process an email that was already stored in the DB by the IMAP worker.
    classification is one of: "pdf_invoice", "financial_body"
    """
    # Load email and its account from the DB
    async with session_factory() as session:
        email_row = await session.get(EmailMessage, email_id)
        if not email_row:
            logger.error(f"Email {email_id} not found in DB — skipping")
            return
        acc = await session.get(EmailAccount, email_row.account_id)
        if not acc:
            logger.error(
                f"Account {email_row.account_id} not found for email {email_id} — skipping"
            )
            return

    logger.info(
        f"Processing email id={email_id} ({classification}) "
        f"from {email_row.from_address}"
    )

    # ── PDF invoice path ──────────────────────────────────────────────────────
    if classification == "pdf_invoice":
        from app.processing.actions.export_pdf import _fetch_pdf_attachments
        att_files = await asyncio.to_thread(_fetch_pdf_attachments, email_row.id)

        if not att_files:
            logger.info(f"Email {email_id}: no PDF attachments — leaving untouched")
            return

        # Scan all PDFs — only proceed if one has a valid ATCUD
        invoice_data = None
        for att_path, att_name in att_files:
            data = await save_invoice_from_pdf(
                session_factory, email_row.id, att_path, settings
            )
            if data and _is_confirmed_at(data):
                invoice_data = data
                logger.info(f"Email {email_id}: ATCUD found in {att_name}")
                break

        if not invoice_data:
            logger.info(
                f"Email {email_id}: no PDF with ATCUD found — leaving untouched"
            )
            return

        # Genuine AT document confirmed — resolve folder via routing table
        target = await _resolve_folder(session_factory, invoice_data)

        if target is None:
            # Document type not in routing table — alert user and wait
            doc_type = invoice_data.get("document_type", "?")
            logger.warning(
                f"Email {email_id}: document type '{doc_type}' has no routing rule — "
                "sending alert, waiting for user to configure"
            )
            await _send_unknown_doctype_alert(
                settings.telegram_bot_token, settings.telegram_chat_id,
                email_row, invoice_data,
            )
            await _set_pending_review(session_factory, email_id, "Faturas")
            return

        trusted = await _is_trusted_seller(session_factory, invoice_data)

        if trusted:
            # Trusted seller — act immediately, notify as filed
            logger.info(f"Email {email_id}: trusted seller — processing immediately")
            await _archive_pdfs(email_row, settings, invoice_data)
            await _move_email(email_row, acc, settings, target)
            await _send_invoice_card(
                settings.telegram_bot_token, settings.telegram_chat_id,
                email_row, invoice_data, target,
                needs_approval=False,
            )
            await _finalize_email(session_factory, email_row.id, target, invoice_data)
        else:
            # New seller — ask for human approval before acting
            logger.info(
                f"Email {email_id}: new seller {email_row.from_address!r} — "
                "sending approval card, waiting for human"
            )
            from app.folders.repository import get_active_folder_names as _get_folders
            async with session_factory() as _s:
                active_folders = await _get_folders(_s)
            await _send_invoice_card(
                settings.telegram_bot_token, settings.telegram_chat_id,
                email_row, invoice_data, target,
                needs_approval=True,
                active_folders=active_folders,
            )
            await _set_pending_review(session_factory, email_id, target)

    # ── Financial body path (temporarily disabled) ────────────────────────────
    elif classification == "financial_body":
        logger.info(
            f"Email {email_id}: financial_body path is disabled — leaving untouched"
        )

    else:
        logger.error(f"Unknown classification '{classification}' for email {email_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Worker loop — Redis consumer
# ─────────────────────────────────────────────────────────────────────────────

async def worker_loop() -> None:
    settings = get_settings()
    logger.info("Invoice Worker starting...")

    engine = make_engine(settings.database_url)
    await init_db(engine)
    logger.info("Database initialized.")

    session_factory = make_session_factory(engine)
    r = redis.from_url(settings.redis_url, decode_responses=True)
    logger.info(f"Connected to Redis. Listening on {INVOICE_QUEUE_KEY} ...")

    while True:
        try:
            # Block up to 5 s, then loop (allows clean shutdown checks)
            job = await r.brpop(INVOICE_QUEUE_KEY, timeout=5)
            if job is None:
                continue

            _, payload_str = job
            payload        = json.loads(payload_str)

            if payload.get("type") == "restart":
                logger.info("🔄 Restart signal received — exiting for Docker to restart.")
                import sys; sys.exit(0)

            email_id       = payload["email_id"]
            classification = payload.get("classification", "pdf_invoice")

            logger.info(
                f"Received invoice job: email_id={email_id} ({classification})"
            )
            await _process_email_by_id(session_factory, settings, email_id, classification)

        except SQLAlchemyError as e:
            logger.error(f"DB error: {e}")
        except Exception:
            logger.exception("Unexpected error in invoice-worker")


def main():
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
