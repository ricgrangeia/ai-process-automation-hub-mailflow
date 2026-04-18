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

# ── Destination IMAP folders ──────────────────────────────────────────────────
_FOLDER_FATURAS    = "Faturas"    # unpaid invoices
_FOLDER_PAGAMENTOS = "Pagamentos" # receipts / payment confirmations

# invoice_origin values that mean "payment already made"
_PAID_ORIGINS = frozenset({
    "receipt", "fatura_recibo", "payment_confirmation", "bank_transfer",
})

# Portuguese AT document_type codes that mean "payment received"
# FR = Fatura-Recibo, RC = Recibo, RG = Recibo Global
_PAID_DOC_TYPES = frozenset({"FR", "RC", "RG"})


def _resolve_folder(invoice_data: dict) -> str:
    """Return the target IMAP folder for this invoice.

    Priority:
      1. document_type from QR code (most reliable — set by AT)
      2. invoice_origin from LLM extraction / keyword rules
      3. Fallback to Faturas (safer: better to file as unpaid than to lose it)
    """
    doc_type = (invoice_data.get("document_type") or "").upper().strip()
    if doc_type in _PAID_DOC_TYPES:
        return _FOLDER_PAGAMENTOS

    origin = (invoice_data.get("invoice_origin") or "").lower().strip()
    if origin in _PAID_ORIGINS:
        return _FOLDER_PAGAMENTOS

    return _FOLDER_FATURAS


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
# Telegram notification
# ─────────────────────────────────────────────────────────────────────────────

async def _notify_telegram(bot_token: str, chat_id: str, message: str) -> None:
    if not bot_token or not chat_id:
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            )
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")


def _build_invoice_message(
    email_row: EmailMessage,
    invoice_data: dict,
    origin: str,
    *,
    moved_to: str | None = None,
    needs_review: bool = False,
) -> str:
    origin_labels = {
        "pt_at_invoice":        "🇵🇹 Fatura AT",
        "pt_at":                "🇵🇹 Fatura AT",
        "fatura_recibo":        "🇵🇹 Fatura-Recibo (pago)",
        "international":        "🌍 International Invoice",
        "payment_confirmation": "💳 Pagamento Confirmado",
        "bank_transfer":        "🏦 Transferência Bancária",
        "receipt":              "🧾 Recibo",
    }
    label = origin_labels.get(origin, origin)

    if needs_review:
        header = f"⚠️ *Needs Review* — {label}"
    else:
        header = f"✅ *Filed* — {label}"

    parts = [header]
    parts.append(f"📧 {email_row.from_address or '?'}")
    if email_row.subject:
        parts.append(f"📋 {email_row.subject}")
    if invoice_data.get("atcud"):
        parts.append(f"ATCUD: `{invoice_data['atcud']}`")
    if invoice_data.get("document_type_description"):
        parts.append(f"Tipo: {invoice_data['document_type_description']}")
    elif invoice_data.get("document_type"):
        parts.append(f"Tipo: {invoice_data['document_type']}")
    if invoice_data.get("nif_seller"):
        parts.append(f"NIF: `{invoice_data['nif_seller']}`")
    if invoice_data.get("seller_name"):
        parts.append(f"Fornecedor: {invoice_data['seller_name']}")
    if invoice_data.get("invoice_number"):
        parts.append(f"Nº: `{invoice_data['invoice_number']}`")
    if invoice_data.get("total_amount") is not None:
        currency = invoice_data.get("currency") or "EUR"
        parts.append(f"Total: *{invoice_data['total_amount']} {currency}*")
    if moved_to:
        parts.append(f"📁 → `{moved_to}`")
    elif needs_review:
        parts.append("_Não movido — verificação manual necessária_")
    return "\n".join(parts)


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
# Invoice review card (Telegram — Approve / Reject buttons)
# ─────────────────────────────────────────────────────────────────────────────

async def _send_invoice_review_card(
    bot_token: str,
    chat_id: str,
    email_row: EmailMessage,
    invoice_data: dict,
    target_folder: str,
) -> None:
    """Send a Telegram card with extracted invoice data and Approve / Reject buttons."""
    if not bot_token or not chat_id:
        return

    parts = [
        "🧾 *New Invoice — Approval Required*",
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

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve & Move",  "callback_data": f"inv_approve:{email_row.id}"},
            {"text": "❌ Reject",           "callback_data": f"inv_reject:{email_row.id}"},
        ]]
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "\n".join(parts),
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard,
                },
            )
        logger.info(f"Sent invoice review card for email {email_row.id}")
    except Exception as e:
        logger.warning(f"Telegram invoice review card failed: {e}")


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

        # Genuine AT document confirmed — resolve folder and act
        target = _resolve_folder(invoice_data)
        trusted = await _is_trusted_seller(session_factory, invoice_data)

        if trusted:
            # Known sender — act immediately
            logger.info(f"Email {email_id}: trusted sender — processing immediately")
            await _archive_pdfs(email_row, settings, invoice_data)
            await _move_email(email_row, acc, settings, target)
            msg = _build_invoice_message(
                email_row, invoice_data,
                invoice_data.get("invoice_origin", "pt_at"),
                moved_to=target,
            )
            await _notify_telegram(settings.telegram_bot_token, settings.telegram_chat_id, msg)
            await _finalize_email(session_factory, email_row.id, target, invoice_data)
        else:
            # New sender — ask for human approval before acting
            logger.info(
                f"Email {email_id}: new sender {email_row.from_address!r} — "
                "sending review card, waiting for approval"
            )
            await _send_invoice_review_card(
                settings.telegram_bot_token, settings.telegram_chat_id,
                email_row, invoice_data, target,
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
