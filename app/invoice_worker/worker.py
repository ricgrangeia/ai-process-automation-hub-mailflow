"""
Invoice Worker — collaborative mode for financial-only mailbox management.

Behaviour:
  - Watches accounts where managed_by = 'invoice_worker'
  - For each unseen email:
      • Has PDF(s)      → extract invoice via tool server → save DB + archive PDF + move email
      • Financial body  → LLM body extraction → save DB + move email
      • Neither         → leave completely untouched (unread, unmoved)
  - Sends a Telegram notification for every successfully extracted invoice
"""

import asyncio
import logging
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential, RetryError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database.engine import make_engine, make_session_factory
from app.core.database.init import init_db
from app.core.crypto import decrypt_secret
from app.accounts.models import EmailAccount
from app.messages.models import EmailMessage, Attachment
from app.messages.storage import save_raw_email, save_attachment
from app.ingestion.parser import parse_email
from app.ingestion.imap.client import connect_imap, fetch_unseen_raw_messages, mark_seen, move_message
from app.invoices.extractor import extract_qr_from_pdf, persist_invoice
from app.invoice_worker.detector import classify_email
from app.invoice_worker.body_extractor import extract_financial_from_body

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("invoice-worker")

# IMAP folder where financial emails land after processing
_DEFAULT_INVOICE_FOLDER = "Invoices"


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


def _build_invoice_message(email_row: EmailMessage, invoice_data: dict, origin: str) -> str:
    origin_labels = {
        "pt_at":               "🇵🇹 AT Invoice",
        "international":       "🌍 International",
        "payment_confirmation": "💳 Payment Confirmation",
        "bank_transfer":       "🏦 Bank Transfer",
        "receipt":             "🧾 Receipt",
    }
    label = origin_labels.get(origin, origin)
    parts = [f"🧾 *Invoice Extracted* — {label}"]
    parts.append(f"📧 {email_row.from_address or '?'}")
    if email_row.subject:
        parts.append(f"📋 {email_row.subject}")
    if invoice_data.get("nif_seller"):
        parts.append(f"NIF Seller: `{invoice_data['nif_seller']}`")
    if invoice_data.get("seller_name"):
        parts.append(f"Seller: {invoice_data['seller_name']}")
    if invoice_data.get("invoice_number"):
        parts.append(f"Invoice #: `{invoice_data['invoice_number']}`")
    if invoice_data.get("total_amount") is not None:
        parts.append(f"Total: *{invoice_data['total_amount']} {invoice_data.get('currency', 'EUR')}*")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# PDF archive helper (reuses export_pdf logic without the EmailAction wrapper)
# ─────────────────────────────────────────────────────────────────────────────

async def _archive_pdfs(email_row: EmailMessage, settings, invoice_data: dict) -> None:
    """Copy PDF attachments to the structured files archive."""
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

    matched = await asyncio.to_thread(_lookup_companies, nif_buyer, nif_seller)
    company_names = [c["name"] for c in matched] if matched else [fallback_company]

    att_files = await asyncio.to_thread(_fetch_pdf_attachments, email_row.id)
    if not att_files and email_row.raw_path:
        att_src = Path(email_row.raw_path).parent / "attachments"
        if att_src.exists():
            att_files = [(p, p.name) for p in att_src.glob("*.pdf")]

    supplier = (email_row.from_address or "Unknown").split("@")[0]
    received_at = getattr(email_row, "received_at", None)

    category = invoice_data.get("invoice_origin", "Invoices").replace("_", " ").title()

    for company_name in company_names:
        dest = _resolve_dest(files_root, company_name, category, supplier, received_at, folder_template)
        dest.mkdir(parents=True, exist_ok=True)
        for att_path, att_name in att_files:
            result = _copy_no_duplicate(att_path, dest, att_name)
            logger.info(f"Archive {att_name} → {dest} ({result})")


# ─────────────────────────────────────────────────────────────────────────────
# Move email to IMAP folder
# ─────────────────────────────────────────────────────────────────────────────

async def _move_email(email_row: EmailMessage, acc: EmailAccount, settings, target_folder: str) -> None:
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
# Process a single email
# ─────────────────────────────────────────────────────────────────────────────

async def _process_email(
    session_factory,
    settings,
    acc: EmailAccount,
    uid: int,
    raw_bytes: bytes,
    uid_for_seen: int,
    imap_conn,
) -> None:
    parsed = parse_email(raw_bytes)

    classification = classify_email(parsed)
    if classification is None:
        logger.info(f"[{acc.username}] UID {uid} — not financial, leaving untouched")
        return  # do NOT mark seen, do NOT move

    async with session_factory() as session:
        exists = await session.execute(
            select(EmailMessage.id).where(
                EmailMessage.account_id == acc.id,
                EmailMessage.imap_uid == uid,
            )
        )
        if exists.scalar_one_or_none() is not None:
            logger.info(f"[{acc.username}] Skipping duplicate UID {uid}")
            return

        email_row = EmailMessage(
            tenant_id=acc.tenant_id,
            account_id=acc.id,
            message_id=parsed["message_id"],
            imap_uid=uid,
            from_name=parsed["from_name"],
            from_address=parsed["from_address"],
            subject=parsed["subject"],
            body_text=parsed["body_text"],
            body_html=parsed["body_html"],
            received_at=parsed["received_at"],
            status="new",
        )
        session.add(email_row)
        await session.flush()

        raw_path = save_raw_email(settings.storage_root, acc.tenant_id, email_row.id, raw_bytes)
        email_row.raw_path = raw_path

        for att in parsed["attachments"]:
            path = save_attachment(
                settings.storage_root, acc.tenant_id, email_row.id,
                att["filename"] or "attachment.bin", att["content"]
            )
            session.add(Attachment(
                email_id=email_row.id,
                filename=att["filename"],
                mime_type=att["mime_type"],
                path=path,
                sha256=att["sha256"],
            ))

        await session.commit()
        logger.info(f"[{acc.username}] Stored email id={email_row.id} ({classification})")

    # ── PDF invoice path ─────────────────────────────────────────────────────
    if classification == "pdf_invoice":
        invoice_data = {}
        # Find stored PDF paths for this email
        from app.processing.actions.export_pdf import _fetch_pdf_attachments
        att_files = await asyncio.to_thread(_fetch_pdf_attachments, email_row.id)
        for att_path, att_name in att_files:
            results = await extract_qr_from_pdf(
                str(att_path),
                settings.tool_server_url,
                settings.tool_server_api_key,
            )
            if results:
                invoice_data = results[0]
                await persist_invoice(session_factory, email_row.id, invoice_data)
                logger.info(f"Persisted invoice for email {email_row.id}")
                break

        if not invoice_data:
            logger.warning(f"No invoice data extracted from PDF(s) in email {email_row.id}")

        # Archive PDFs regardless of extraction success
        await _archive_pdfs(email_row, settings, invoice_data)

        # Move email
        target = _DEFAULT_INVOICE_FOLDER
        await _move_email(email_row, acc, settings, target)

        # Mark stored email status
        async with session_factory() as session:
            row = await session.get(EmailMessage, email_row.id)
            if row:
                row.status = "moved"
                row.classification_label = target
                await session.commit()

        # Notify Telegram
        if invoice_data:
            msg = _build_invoice_message(email_row, invoice_data, invoice_data.get("invoice_origin", "pt_at"))
            await _notify_telegram(settings.telegram_bot_token, settings.telegram_chat_id, msg)

    # ── Financial body path ──────────────────────────────────────────────────
    elif classification == "financial_body":
        import os
        lang = os.environ.get("LANGUAGE", "en")
        result = await extract_financial_from_body(
            subject=parsed.get("subject", ""),
            body_text=parsed.get("body_text", ""),
            llm_base_url=settings.llm_base_url,
            llm_api_key=settings.llm_api_key,
            llm_model=settings.llm_model,
            language=lang,
        )

        if result:
            origin = result.get("invoice_origin") or "payment_confirmation"
            result["invoice_origin"] = origin
            await persist_invoice(session_factory, email_row.id, result)
            logger.info(f"Persisted financial body data for email {email_row.id} (origin={origin})")

            target = _DEFAULT_INVOICE_FOLDER
            await _move_email(email_row, acc, settings, target)

            async with session_factory() as session:
                row = await session.get(EmailMessage, email_row.id)
                if row:
                    row.status = "moved"
                    row.classification_label = target
                    await session.commit()

            msg = _build_invoice_message(email_row, result, origin)
            await _notify_telegram(settings.telegram_bot_token, settings.telegram_chat_id, msg)
        else:
            logger.warning(f"LLM body extraction returned nothing for email {email_row.id}")
            # Leave email unread/unmoved — human will deal with it

    # Mark seen after successful processing
    if settings.mark_seen_after_store:
        try:
            mark_seen(imap_conn, settings.inbox_folder, uid_for_seen)
        except Exception as e:
            logger.warning(f"mark_seen failed for UID {uid}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Account poll
# ─────────────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
async def process_account_once(settings, session_factory, acc: EmailAccount) -> None:
    if acc.provider != "imap":
        return
    if not acc.imap_host or not acc.username:
        logger.warning(f"[account id={acc.id}] Incomplete config. Skipping.")
        return

    logger.info(f"[{acc.username}] Checking account (invoice-worker)...")
    password = decrypt_secret(settings.master_key, acc.password_encrypted)

    def _fetch():
        conn = connect_imap(acc.imap_host, acc.imap_port or 993, acc.username, password)
        try:
            messages = list(fetch_unseen_raw_messages(conn, settings.inbox_folder, settings.max_unseen_per_cycle))
            return messages, conn
        except Exception:
            conn.logout()
            raise

    try:
        messages, conn = await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.error(f"[{acc.username}] IMAP connection failed: {e}")
        raise

    if not messages:
        logger.info(f"[{acc.username}] No new messages.")
        return

    logger.info(f"[{acc.username}] Fetched {len(messages)} messages.")

    for uid, raw_bytes, uid_for_seen in messages:
        try:
            await _process_email(session_factory, settings, acc, uid, raw_bytes, uid_for_seen, conn)
        except Exception as e:
            logger.exception(f"[{acc.username}] Error processing UID {uid}: {e}")

    try:
        conn.logout()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Worker loop
# ─────────────────────────────────────────────────────────────────────────────

async def worker_loop() -> None:
    settings = get_settings()
    logger.info("Invoice Worker starting...")

    engine = make_engine(settings.database_url)
    await init_db(engine)
    logger.info("Database initialized.")

    session_factory = make_session_factory(engine)

    while True:
        try:
            logger.info("Invoice Worker poll cycle started.")

            async with session_factory() as session:
                res = await session.execute(
                    select(EmailAccount).where(
                        EmailAccount.active == True,
                        EmailAccount.provider == "imap",
                        EmailAccount.managed_by == "invoice_worker",
                    )
                )
                accounts = list(res.scalars().all())

            logger.info(f"Found {len(accounts)} invoice-worker account(s).")

            sem = asyncio.Semaphore(5)

            async def _run(acc):
                async with sem:
                    await process_account_once(settings, session_factory, acc)

            await asyncio.gather(*[_run(a) for a in accounts])

        except RetryError as e:
            logger.exception(f"Retry failed: {e.last_attempt.exception()}")
        except SQLAlchemyError as e:
            logger.error(f"DB error: {e}")
        except Exception:
            logger.exception("Unexpected error in invoice-worker")

        logger.info(f"Sleeping {settings.poll_interval_sec}s...\n")
        await asyncio.sleep(settings.poll_interval_sec)


def main():
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
