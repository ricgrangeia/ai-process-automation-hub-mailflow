"""
Telegram Bot — NeedsReview callback handler.

Callback data formats:
  classify:{email_id}:{folder}            — user picked a folder
  folder_new_request:{email_id}          — user wants to type a new folder name
  folder_suggest_add:{email_id}:{folder} — user approved AI-suggested new folder
  learn_move:{email_id}:{folder}          — save rule: move only
  learn_pdf:{email_id}:{folder}:{path}    — save rule: export PDF only
  learn_both:{email_id}:{folder}:{path}   — save rule: move + export PDF
  learn_ask_path:{email_id}:{folder}      — ask for PDF export path
  skip_learn:{email_id}                   — no rule saved
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters, ContextTypes

# Ensure project root is in path
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from sqlalchemy import select, update as sa_update

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.database.engine import make_engine, make_session_factory
from app.accounts.models import EmailAccount
from app.messages.models import EmailMessage
from app.classification.learned_rules import LearnedRule
from app.ingestion.imap.client import connect_imap, move_message
from app.processing.queue import QUEUE_KEY as EMAIL_QUEUE_KEY
from app.query.queue import QUERY_QUEUE_KEY, RESULT_KEY_PREFIX
from app.review.queue import REVIEW_QUEUE_KEY, LEARNING_MODE_KEY
from app.core.audit import log_audit, _telegram_actor
from app.core.operation_mode import get_mode, MODES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("telegram-bot")

# Stores pending path input per chat: {chat_id: (email_id, folder)}
_pending_path: dict[int, tuple[int, str]] = {}
# Stores pending new-folder name input per chat: {chat_id: email_id}
_pending_new_folder: dict[int, int] = {}

DEFAULT_PDF_PATH = "Exports/{year}/{month}/"


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def get_session_factory():
    settings = get_settings()
    engine = make_engine(settings.database_url)
    return make_session_factory(engine), settings


async def _do_move(settings, email, account, folder: str) -> bool:
    if not account or account.provider != "imap":
        return True

    imap_password = decrypt_secret(settings.master_key, account.password_encrypted)

    def _move():
        conn = connect_imap(
            account.imap_host,
            account.imap_port or 993,
            account.username,
            imap_password,
        )
        try:
            move_message(conn, settings.inbox_folder, folder, email.imap_uid)
            return True
        except Exception as e:
            logger.error(f"IMAP move error for email {email.id}: {e}")
            return False
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    return await asyncio.to_thread(_move)


async def _save_rule(session_factory, email, folder: str, actions: list) -> str | None:
    """Persists a LearnedRule. Returns the match_value used (domain) or None."""
    if not email or not email.from_address:
        return None

    domain = email.from_address.split("@")[-1].lower() if "@" in email.from_address else None
    if not domain:
        return None

    async with session_factory() as session:
        # Upsert: update if a rule for this domain already exists
        existing = await session.execute(
            select(LearnedRule).where(
                LearnedRule.tenant_id == email.tenant_id,
                LearnedRule.match_field == "sender_domain",
                LearnedRule.match_value == domain,
            )
        )
        rule = existing.scalar_one_or_none()

        if rule:
            rule.actions = actions
            rule.active = True
        else:
            session.add(LearnedRule(
                tenant_id=email.tenant_id,
                match_field="sender_domain",
                match_value=domain,
                actions=actions,
                created_from_email_id=email.id,
            ))

        await session.commit()

    logger.info(f"📚 Saved learned rule: sender_domain={domain} → actions={actions}")
    return domain


# ------------------------------------------------------------------------------
# Handler: classify:{email_id}:{folder}
# ------------------------------------------------------------------------------

async def handle_classify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, email_id_str, folder = query.data.split(":", 2)
    email_id = int(email_id_str)

    session_factory, settings = get_session_factory()

    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()

        if not email:
            await query.edit_message_text(f"❌ Email {email_id} not found in DB.")
            return

        account = (await session.execute(
            select(EmailAccount).where(EmailAccount.id == email.account_id)
        )).scalar_one_or_none()

    move_success = await _do_move(settings, email, account, folder)

    async with session_factory() as session:
        await session.execute(
            sa_update(EmailMessage)
            .where(EmailMessage.id == email_id)
            .values(
                status="moved" if move_success else "failed_move",
                classification_label=folder,
                ai_source="human",
                processed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    await log_audit(
        session_factory,
        actor_type="telegram",
        actor_name=_telegram_actor(query.from_user),
        action="email.reclassified",
        entity_type="email",
        entity_id=email_id,
        tenant_id=getattr(email, "tenant_id", None),
        details={"folder": folder, "move_success": move_success},
    )

    # Invoice QR extraction — fire and forget
    if move_success and ("invoice" in folder.lower() or "fatura" in folder.lower()):
        try:
            from app.invoices.extractor import extract_qr_from_pdf, persist_invoice
            from pathlib import Path as _Path
            if email.raw_path:
                att_dir = _Path(email.raw_path).parent / "attachments"
                pdfs = list(att_dir.glob("*.pdf")) if att_dir.exists() else []
                for pdf in pdfs:
                    results = await extract_qr_from_pdf(
                        str(pdf), settings.tool_server_url, settings.tool_server_api_key
                    )
                    for invoice_data in results:
                        await persist_invoice(session_factory, email_id, invoice_data)
                    if results:
                        break
        except Exception as _e:
            logger.warning(f"Invoice QR extraction failed for email {email_id}: {_e}")

    status_icon = "✅" if move_success else "⚠️"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes — always move to this folder", callback_data=f"learn_move:{email_id}:{folder}")],
        [InlineKeyboardButton("✅ Yes — also export PDF",            callback_data=f"learn_ask_path:{email_id}:{folder}:with_move")],
        [InlineKeyboardButton("🚫 No — just this once",              callback_data=f"skip_learn:{email_id}")],
    ])

    await query.edit_message_text(
        f"{status_icon} Moved to *{folder}*.\n\n"
        f"Remember this for future emails from the same sender?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ------------------------------------------------------------------------------
# Handler: learn_move:{email_id}:{folder}  — save move-only rule
# ------------------------------------------------------------------------------

async def handle_learn_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    email_id = int(parts[1])
    folder = parts[2]

    session_factory, _ = get_session_factory()

    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()

    actions = [{"type": "move_folder", "folder": folder}]
    domain = await _save_rule(session_factory, email, folder, actions)

    if domain:
        await log_audit(
            session_factory,
            actor_type="telegram",
            actor_name=_telegram_actor(query.from_user),
            action="rule.created",
            entity_type="rule",
            entity_id=None,
            tenant_id=getattr(email, "tenant_id", None),
            details={"domain": domain, "folder": folder, "actions": actions},
        )
        await query.edit_message_text(
            f"📚 Rule saved — emails from *{domain}* will be moved to *{folder}*.",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text("⚠️ Could not extract sender domain to create a rule.")


# ------------------------------------------------------------------------------
# Handler: learn_ask_path:{email_id}:{folder}[:with_move]
# Asks user to type a PDF export path or accept the default
# ------------------------------------------------------------------------------

async def handle_learn_ask_path(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    email_id = int(parts[1])
    folder = parts[2]
    with_move = len(parts) > 3 and parts[3] == "with_move"

    chat_id = query.message.chat_id
    # Store state so the next plain text message is treated as path input
    _pending_path[chat_id] = (email_id, folder, with_move)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"Use default: {DEFAULT_PDF_PATH}",
            callback_data=f"learn_pdf:{email_id}:{folder}:{'1' if with_move else '0'}:{DEFAULT_PDF_PATH}"
        )]
    ])

    await query.edit_message_text(
        f"📂 Type the export path for PDF files, or use the default.\n\n"
        f"Supported variables: `{{year}}`, `{{month}}`, `{{day}}`\n\n"
        f"Example: `Company/{{year}}/{{month}}/Payments/`",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ------------------------------------------------------------------------------
# Handler: learn_pdf:{email_id}:{folder}:{with_move}:{path}
# ------------------------------------------------------------------------------

async def handle_learn_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 4)
    email_id = int(parts[1])
    folder = parts[2]
    with_move = parts[3] == "1"
    path = parts[4]

    await _persist_pdf_rule(query, email_id, folder, with_move, path)


async def _persist_pdf_rule(query_or_message, email_id, folder, with_move, path):
    session_factory, _ = get_session_factory()

    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()

    actions = []
    if with_move:
        actions.append({"type": "move_folder", "folder": folder})
    actions.append({"type": "export_pdf", "path": path})

    domain = await _save_rule(session_factory, email, folder, actions)

    label = "Move & Export PDF" if with_move else "Export PDF"
    msg = (
        f"📚 Rule saved — *{label}* for emails from *{domain}*.\n"
        f"PDF path: `{path}`"
        if domain else
        "⚠️ Could not extract sender domain to create a rule."
    )

    if hasattr(query_or_message, 'edit_message_text'):
        await query_or_message.edit_message_text(msg, parse_mode="Markdown")
    else:
        await query_or_message.reply_text(msg, parse_mode="Markdown")


# ------------------------------------------------------------------------------
# Handler: plain text message
#   — if chat is awaiting a PDF path, treat text as the path
#   — otherwise treat it as a natural language email search query
# ------------------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # PDF path input takes priority
    if chat_id in _pending_path:
        email_id, folder, with_move = _pending_path.pop(chat_id)
        await _persist_pdf_rule(update.message, email_id, folder, with_move, text)
        return

    # New folder name input
    if chat_id in _pending_new_folder:
        email_id = _pending_new_folder.pop(chat_id)
        folder_name = text.strip()
        if not folder_name:
            await update.message.reply_text("⚠️ Folder name cannot be empty. Tap ➕ New folder again to retry.")
            return

        session_factory, settings = get_session_factory()

        # Load email + account
        async with session_factory() as session:
            email = (await session.execute(
                select(EmailMessage).where(EmailMessage.id == email_id)
            )).scalar_one_or_none()
            account = (await session.execute(
                select(EmailAccount).where(EmailAccount.id == email.account_id)
            )).scalar_one_or_none() if email else None

        if not email or not account:
            await update.message.reply_text("⚠️ Email not found.")
            return

        # 1. Create folder in DB (idempotent)
        async with session_factory() as session:
            from sqlalchemy import text as _text
            existing = await session.execute(
                _text("SELECT id FROM folders WHERE name = :name"),
                {"name": folder_name},
            )
            if not existing.scalar_one_or_none():
                await session.execute(
                    _text("INSERT INTO folders (name, is_active) VALUES (:name, true)"),
                    {"name": folder_name},
                )
                await session.commit()

        # 2. Create IMAP folder on all active accounts
        from app.ingestion.imap.client import connect_imap as _connect_imap, ensure_folder_exists as _ensure_folder
        from app.core.crypto import decrypt_secret as _decrypt
        from app.core.database.engine import make_engine as _make_engine
        import pandas as _pd
        from sqlalchemy import text as _text2

        imap_results = []
        try:
            _engine = _make_engine(settings.database_url)
            accounts_df = _pd.read_sql(
                "SELECT id, imap_host, imap_port, username, password_encrypted "
                "FROM email_accounts WHERE active = true AND provider = 'imap'",
                _engine,
            )
            for _, acc in accounts_df.iterrows():
                try:
                    pw = _decrypt(settings.master_key, acc["password_encrypted"])
                    conn_imap = _connect_imap(acc["imap_host"], int(acc["imap_port"] or 993), acc["username"], pw)
                    _ensure_folder(conn_imap, folder_name)
                    conn_imap.logout()
                    imap_results.append(f"✅ {acc['username']}")
                except Exception as ie:
                    imap_results.append(f"⚠️ {acc['username']}: {ie}")
        except Exception as e:
            imap_results.append(f"⚠️ Could not load accounts: {e}")

        # 3. Move the email
        await _do_move(settings, email, account, folder_name)

        # 4. Update DB status
        async with session_factory() as session:
            await session.execute(
                sa_update(EmailMessage)
                .where(EmailMessage.id == email_id)
                .values(
                    status="moved",
                    classification_label=folder_name,
                    processed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        await log_audit(
            session_factory,
            actor_type="telegram",
            actor_name=_telegram_actor(update.effective_user),
            action="folder.created_from_review",
            entity_type="folder",
            details={"name": folder_name, "email_id": email_id},
            tenant_id=getattr(email, "tenant_id", None),
        )

        imap_summary = " | ".join(imap_results) if imap_results else "no IMAP accounts"
        await update.message.reply_text(
            f"✅ Folder '{folder_name}' created and email moved.\n\nIMAP: {imap_summary}"
        )
        return

    # Sender name input
    if chat_id in _pending_sender_name:
        email_id = _pending_sender_name.pop(chat_id)
        session_factory, _ = get_session_factory()
        async with session_factory() as session:
            email_row = (await session.execute(
                select(EmailMessage).where(EmailMessage.id == email_id)
            )).scalar_one_or_none()
            if email_row:
                await session.execute(
                    sa_update(EmailMessage)
                    .where(EmailMessage.id == email_id)
                    .values(sender_name=text)
                )
                await session.commit()
        await log_audit(
            session_factory,
            actor_type="telegram",
            actor_name=_telegram_actor(update.effective_user),
            action="email.sender_corrected",
            entity_type="email",
            entity_id=email_id,
            tenant_id=getattr(email_row, "tenant_id", None) if email_row else None,
            details={"sender_name": text},
        )
        await update.message.reply_text(f"✅ Sender name saved: {text}")
        return

    # Treat as email search query — delegate to query-worker via Redis
    await _enqueue_query(update, text)


async def _enqueue_query(update: Update, query_text: str):
    """Push a query job to Redis for the query-worker to process."""
    session_factory, settings = get_session_factory()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    job = json.dumps({
        "type": "query",
        "chat_id": str(update.effective_chat.id),
        "tenant_id": 1,
        "query_text": query_text,
    })
    await r.lpush(QUERY_QUEUE_KEY, job)
    await r.aclose()
    await log_audit(
        session_factory,
        actor_type="telegram",
        actor_name=_telegram_actor(update.effective_user),
        action="query.searched",
        entity_type="query",
        details={"query_text": query_text},
        tenant_id=1,
    )
    await update.message.reply_text("🔍 On it… I'll send the results shortly.")


# ------------------------------------------------------------------------------
# Handler: /search <query>
# ------------------------------------------------------------------------------

async def handle_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = " ".join(context.args).strip() if context.args else ""
    if not query_text:
        await update.message.reply_text(
            "Usage: `/search <query>`\n\nExample: `/search invoices from amazon.com January 2026`",
            parse_mode="Markdown",
        )
        return
    await _enqueue_query(update, query_text)


# ------------------------------------------------------------------------------
# Handlers: query_show:{result_id} / query_email:{result_id}
# Push a delivery job back to the query-worker via Redis
# ------------------------------------------------------------------------------

async def handle_query_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    result_id = query.data.split(":", 1)[1]
    await _push_delivery_job(query, result_id, "inline")
    await query.edit_message_text("📱 Fetching results…")


async def handle_query_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    result_id = query.data.split(":", 1)[1]
    await _push_delivery_job(query, result_id, "email")
    await query.edit_message_text("📧 Sending results by email…")


async def _push_delivery_job(query, result_id: str, method: str):
    _, settings = get_session_factory()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    job = json.dumps({
        "type": "query_deliver",
        "result_id": result_id,
        "method": method,
        "chat_id": str(query.message.chat_id),
    })
    await r.lpush(QUERY_QUEUE_KEY, job)
    await r.aclose()


# ------------------------------------------------------------------------------
# Review handlers: rv_approve / rv_folder / rv_set_folder / rv_save_rule /
#                  rv_skip_rule / rv_sender / rv_set_sender
# ------------------------------------------------------------------------------

# Pending sender name input: {chat_id: email_id}
_pending_sender_name: dict[int, int] = {}


async def handle_rv_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, email_id_str, folder = query.data.split(":", 2)
    email_id = int(email_id_str)

    session_factory, settings = get_session_factory()

    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()
        account = (await session.execute(
            select(EmailAccount).where(EmailAccount.id == email.account_id)
        )).scalar_one_or_none() if email else None

    if email and account:
        await _do_move(settings, email, account, folder)

    async with session_factory() as session:
        await session.execute(
            sa_update(EmailMessage)
            .where(EmailMessage.id == email_id)
            .values(status="moved", classification_label=folder,
                    processed_at=datetime.now(timezone.utc))
        )
        await session.commit()

    await log_audit(
        session_factory,
        actor_type="telegram",
        actor_name=_telegram_actor(query.from_user),
        action="email.approved",
        entity_type="email",
        entity_id=email_id,
        tenant_id=getattr(email, "tenant_id", None),
        details={"folder": folder},
    )
    await query.edit_message_text(f"✅ Approved → *{folder}*. No rule saved.", parse_mode="Markdown")


async def handle_rv_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    email_id = parts[1]
    current = parts[2] if len(parts) > 2 else ""

    from app.folders.repository import get_active_folder_names
    session_factory, _ = get_session_factory()
    async with session_factory() as session:
        folder_names = await get_active_folder_names(session)

    keyboard = [
        [{"text": f"{'✅ ' if f == current else ''}📁 {f}",
          "callback_data": f"rv_set_folder:{email_id}:{f}"}]
        for f in folder_names
    ]
    await query.edit_message_text(
        "📁 Choose the correct folder:",
        reply_markup={"inline_keyboard": keyboard},
    )


async def handle_rv_set_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, email_id_str, folder = query.data.split(":", 2)
    email_id = int(email_id_str)

    session_factory, settings = get_session_factory()

    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()
        account = (await session.execute(
            select(EmailAccount).where(EmailAccount.id == email.account_id)
        )).scalar_one_or_none() if email else None

    if email and account:
        await _do_move(settings, email, account, folder)

    async with session_factory() as session:
        await session.execute(
            sa_update(EmailMessage)
            .where(EmailMessage.id == email_id)
            .values(status="moved", classification_label=folder,
                    processed_at=datetime.now(timezone.utc))
        )
        await session.commit()

    session_factory2, _ = get_session_factory()
    await log_audit(
        session_factory2,
        actor_type="telegram",
        actor_name=_telegram_actor(query.from_user),
        action="email.reclassified",
        entity_type="email",
        entity_id=email_id,
        tenant_id=getattr(email, "tenant_id", None),
        details={"folder": folder, "via": "review_card"},
    )

    keyboard = [
        [{"text": "💾 Save as rule", "callback_data": f"rv_save_rule:{email_id}:{folder}"}],
        [{"text": "Skip — just this once", "callback_data": f"rv_skip_rule:{email_id}"}],
    ]
    await query.edit_message_text(
        f"📁 Moved to *{folder}*.\n\nSave as a learned rule for future emails from this sender?",
        parse_mode="Markdown",
        reply_markup={"inline_keyboard": keyboard},
    )


async def handle_rv_save_rule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, email_id_str, folder = query.data.split(":", 2)
    email_id = int(email_id_str)

    session_factory, _ = get_session_factory()
    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()

    actions = [{"type": "move_folder", "folder": folder}]
    domain = await _save_rule(session_factory, email, folder, actions)

    if domain:
        await log_audit(
            session_factory,
            actor_type="telegram",
            actor_name=_telegram_actor(query.from_user),
            action="rule.created",
            entity_type="rule",
            entity_id=None,
            tenant_id=getattr(email, "tenant_id", None),
            details={"domain": domain, "folder": folder, "via": "review_card"},
        )
        await query.edit_message_text(
            f"📚 Rule saved — *{domain}* → *{folder}*.", parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("⚠️ Could not extract sender domain.")


async def handle_rv_skip_rule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👍 Done — no rule saved.")


async def handle_rv_sender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    email_id = query.data.split(":")[1]

    keyboard = [
        [{"text": "🏢 Company", "callback_data": f"rv_set_sender:{email_id}:company"}],
        [{"text": "👤 Person",  "callback_data": f"rv_set_sender:{email_id}:person"}],
    ]
    await query.edit_message_text(
        "👤 Is the sender a company or a person?",
        reply_markup={"inline_keyboard": keyboard},
    )


async def handle_rv_set_sender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, email_id_str, sender_type = query.data.split(":", 2)
    email_id = int(email_id_str)
    chat_id = query.message.chat_id

    # Save type immediately, then ask for name via free text
    session_factory, _ = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            sa_update(EmailMessage)
            .where(EmailMessage.id == email_id)
            .values(sender_type=sender_type)
        )
        await session.commit()

    _pending_sender_name[chat_id] = email_id
    icon = "🏢" if sender_type == "company" else "👤"
    await query.edit_message_text(
        f"{icon} Sender type saved as *{sender_type}*.\n\n"
        f"Type the sender name (e.g. 'Amazon', 'João Silva') or send /skip to leave it as-is.",
        parse_mode="Markdown",
    )


# ------------------------------------------------------------------------------
# Admin commands: /status, /recover, /restart, /learn
# ------------------------------------------------------------------------------

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_factory, settings = get_session_factory()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    try:
        async with session_factory() as session:
            from sqlalchemy import text
            rows = await session.execute(text(
                "SELECT status, COUNT(*) as n FROM emails GROUP BY status ORDER BY status"
            ))
            counts = rows.all()

        email_q = await r.llen(EMAIL_QUEUE_KEY)
        query_q = await r.llen(QUERY_QUEUE_KEY)
        op_mode = await get_mode(r)
        await r.aclose()

        db_lines = "\n".join(f"  {row[0]}: {row[1]}" for row in counts) or "  (no emails)"
        mode_label = MODES.get(op_mode, op_mode)
        msg = (
            f"📊 System Status\n\n"
            f"DB — Emails by status:\n{db_lines}\n\n"
            f"Redis queues:\n"
            f"  email jobs pending: {email_q}\n"
            f"  query jobs pending: {query_q}\n\n"
            f"Operation Mode:\n"
            f"  {mode_label}"
        )
    except Exception as e:
        msg = f"⚠️ Status check failed: {e}"

    await update.message.reply_text(msg)


async def handle_recover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_factory, settings = get_session_factory()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    try:
        async with session_factory() as session:
            from sqlalchemy import text
            result = await session.execute(text(
                "UPDATE emails SET status='new', processed_at=NULL "
                "WHERE status='pending_review' RETURNING id, tenant_id"
            ))
            rows = result.all()
            await session.commit()

        ids = [str(row[0]) for row in rows]

        if ids:
            # Re-enqueue immediately so ai-worker picks them up without waiting
            for email_id, tenant_id in rows:
                payload = json.dumps({
                    "tenant_id": tenant_id,
                    "email_id": email_id,
                    "type": "process_email",
                    "retries": 0,
                })
                await r.lpush(EMAIL_QUEUE_KEY, payload)

            msg = f"♻️ Reset and re-queued {len(ids)} email(s): {', '.join(ids)}\n\nAI worker will reprocess them now."
            await log_audit(
                session_factory,
                actor_type="telegram",
                actor_name=_telegram_actor(update.effective_user),
                action="system.recover",
                entity_type="system",
                details={"reset_ids": ids},
            )
        else:
            msg = "✅ No emails stuck in pending_review."
    except Exception as e:
        msg = f"⚠️ Recovery failed: {e}"
    finally:
        await r.aclose()

    await update.message.reply_text(msg)


async def handle_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0] if context.args else "").lower()
    _, settings = get_session_factory()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    if arg == "on":
        await r.set(LEARNING_MODE_KEY, "1")
        await r.aclose()
        session_factory, _ = get_session_factory()
        await log_audit(
            session_factory,
            actor_type="telegram",
            actor_name=_telegram_actor(update.effective_user),
            action="system.learning_mode",
            entity_type="system",
            details={"state": "on"},
        )
        await update.message.reply_text(
            "🎓 *Learning Mode ON*\n\n"
            "Every new email (except learned rule matches) will be sent for your review.\n"
            "You'll see the AI decision, sender identity, and can approve or correct it.\n"
            "Corrections will prompt to save as a learned rule.",
            parse_mode="Markdown",
        )
    elif arg == "off":
        await r.delete(LEARNING_MODE_KEY)
        await r.aclose()
        session_factory, _ = get_session_factory()
        await log_audit(
            session_factory,
            actor_type="telegram",
            actor_name=_telegram_actor(update.effective_user),
            action="system.learning_mode",
            entity_type="system",
            details={"state": "off"},
        )
        await update.message.reply_text("✅ *Learning Mode OFF* — running autonomously.", parse_mode="Markdown")
    else:
        is_on = await r.exists(LEARNING_MODE_KEY)
        await r.aclose()
        status = "🟢 ON" if is_on else "🔴 OFF"
        await update.message.reply_text(
            f"🎓 Learning Mode is currently *{status}*.\n\nUse `/learn on` or `/learn off`.",
            parse_mode="Markdown",
        )


async def handle_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, settings = get_session_factory()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    restart_job = json.dumps({"type": "restart"})
    await r.lpush(EMAIL_QUEUE_KEY, restart_job)
    await r.lpush(QUERY_QUEUE_KEY, restart_job)
    await r.lpush(REVIEW_QUEUE_KEY, restart_job)
    await r.aclose()

    session_factory, _ = get_session_factory()
    await log_audit(
        session_factory,
        actor_type="telegram",
        actor_name=_telegram_actor(update.effective_user),
        action="system.restart",
        entity_type="system",
        details={"targets": ["ai-worker", "query-worker", "review-worker"]},
    )

    await update.message.reply_text(
        "🔄 Restart signal sent to *ai-worker*, *query-worker* and *review-worker*.\n"
        "Docker will restart them automatically.\n\n"
        "You'll receive a startup notification when ai-worker is back online.",
        parse_mode="Markdown",
    )


# ------------------------------------------------------------------------------
# Handler: skip_learn:{email_id}
# ------------------------------------------------------------------------------

async def handle_skip_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👍 Done — no rule saved.")


# ------------------------------------------------------------------------------
# Handler: folder_suggest_add:{email_id}:{folder_name}
# LLM suggested a new folder that doesn't exist yet — user approved adding it.
# ------------------------------------------------------------------------------

async def handle_folder_suggest_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    email_id = int(parts[1])
    folder_name = parts[2]

    session_factory, settings = get_session_factory()

    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()
        account = (await session.execute(
            select(EmailAccount).where(EmailAccount.id == email.account_id)
        )).scalar_one_or_none() if email else None

    if not email or not account:
        await query.edit_message_text("⚠️ Email not found.")
        return

    # 1. Create the folder in DB if it doesn't exist yet
    async with session_factory() as session:
        from sqlalchemy import text as _text
        existing = await session.execute(
            _text("SELECT id FROM folders WHERE name = :name"),
            {"name": folder_name},
        )
        if not existing.scalar_one_or_none():
            await session.execute(
                _text("INSERT INTO folders (name, is_active) VALUES (:name, true)"),
                {"name": folder_name},
            )
            await session.commit()

    # 2. Create IMAP folder on all active accounts
    from app.ingestion.imap.client import connect_imap as _connect_imap, ensure_folder_exists as _ensure_folder
    from app.core.database.engine import make_engine as _make_engine
    import pandas as _pd
    try:
        _engine = _make_engine(settings.database_url)
        accounts_df = _pd.read_sql(
            "SELECT id, imap_host, imap_port, username, password_encrypted "
            "FROM email_accounts WHERE active = true AND provider = 'imap'",
            _engine,
        )
        for _, acc in accounts_df.iterrows():
            try:
                pw = decrypt_secret(settings.master_key, acc["password_encrypted"])
                conn_imap = _connect_imap(acc["imap_host"], int(acc["imap_port"] or 993), acc["username"], pw)
                _ensure_folder(conn_imap, folder_name)
                conn_imap.logout()
            except Exception as ie:
                logger.warning(f"IMAP folder create failed for {acc['username']}: {ie}")
    except Exception as e:
        logger.warning(f"Could not create IMAP folder on accounts: {e}")

    # 3. Move the email to the new IMAP folder
    await _do_move(settings, email, account, folder_name)

    # 4. Update email status in DB
    async with session_factory() as session:
        await session.execute(
            sa_update(EmailMessage)
            .where(EmailMessage.id == email_id)
            .values(
                status="moved",
                classification_label=folder_name,
                processed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    await log_audit(
        session_factory,
        actor_type="telegram",
        actor_name=_telegram_actor(query.from_user),
        action="folder.created_from_suggestion",
        entity_type="folder",
        details={"name": folder_name, "email_id": email_id},
        tenant_id=getattr(email, "tenant_id", None),
    )

    await query.edit_message_text(
        f"✅ Folder '{folder_name}' created and email moved."
    )


# ------------------------------------------------------------------------------
# Handler: folder_new_request:{email_id}
# User tapped "➕ New folder" on any NeedsReview card — ask them to type a name.
# ------------------------------------------------------------------------------

async def handle_folder_new_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 1)
    email_id = int(parts[1])
    chat_id = update.effective_chat.id

    _pending_new_folder[chat_id] = email_id
    await query.edit_message_text(
        f"✏️ Type the new folder name to create and move email #{email_id} there:\n\n"
        f"(e.g. Legal, Finance/Invoices, Work/Clients)"
    )


# ------------------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------------------

def main():
    settings = get_settings()

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")
        return

    async def post_init(application):
        await application.bot.set_my_commands([
            ("status",  "📊 System status — DB counts + queue depths"),
            ("recover", "♻️ Reset stuck pending_review emails to new"),
            ("restart", "🔄 Restart ai-worker and query-worker"),
            ("learn",   "🎓 Toggle learning mode — /learn on | off | (status)"),
            ("search",  "🔍 Search emails by folder, sender, date or keyword"),
        ])

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("status",  handle_status))
    app.add_handler(CommandHandler("recover", handle_recover))
    app.add_handler(CommandHandler("restart", handle_restart))
    app.add_handler(CommandHandler("learn",   handle_learn))
    app.add_handler(CommandHandler("search",  handle_search_command))
    # NeedsReview callbacks
    app.add_handler(CallbackQueryHandler(handle_folder_suggest_add,  pattern=r"^folder_suggest_add:"))
    app.add_handler(CallbackQueryHandler(handle_folder_new_request,  pattern=r"^folder_new_request:"))
    app.add_handler(CallbackQueryHandler(handle_classify,            pattern=r"^classify:"))
    app.add_handler(CallbackQueryHandler(handle_learn_move,     pattern=r"^learn_move:"))
    app.add_handler(CallbackQueryHandler(handle_learn_ask_path, pattern=r"^learn_ask_path:"))
    app.add_handler(CallbackQueryHandler(handle_learn_pdf,      pattern=r"^learn_pdf:"))
    app.add_handler(CallbackQueryHandler(handle_skip_learn,     pattern=r"^skip_learn:"))
    # Review (learning mode) callbacks
    app.add_handler(CallbackQueryHandler(handle_rv_approve,    pattern=r"^rv_approve:"))
    app.add_handler(CallbackQueryHandler(handle_rv_folder,     pattern=r"^rv_folder:"))
    app.add_handler(CallbackQueryHandler(handle_rv_set_folder, pattern=r"^rv_set_folder:"))
    app.add_handler(CallbackQueryHandler(handle_rv_save_rule,  pattern=r"^rv_save_rule:"))
    app.add_handler(CallbackQueryHandler(handle_rv_skip_rule,  pattern=r"^rv_skip_rule:"))
    app.add_handler(CallbackQueryHandler(handle_rv_sender,     pattern=r"^rv_sender:"))
    app.add_handler(CallbackQueryHandler(handle_rv_set_sender, pattern=r"^rv_set_sender:"))
    # Query callbacks
    app.add_handler(CallbackQueryHandler(handle_query_show,    pattern=r"^query_show:"))
    app.add_handler(CallbackQueryHandler(handle_query_email,   pattern=r"^query_email:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 Telegram bot started — polling for callbacks...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
