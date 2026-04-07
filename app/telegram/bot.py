"""
Telegram Bot — NeedsReview callback handler.

Callback data formats:
  classify:{email_id}:{folder}            — user picked a folder
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

from sqlalchemy import select, update

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("telegram-bot")

# Stores pending path input per chat: {chat_id: (email_id, folder)}
_pending_path: dict[int, tuple[int, str]] = {}

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
            update(EmailMessage)
            .where(EmailMessage.id == email_id)
            .values(
                status="moved" if move_success else "failed_move",
                classification_label=folder,
                ai_source="human",
                processed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    status_icon = "✅" if move_success else "⚠️"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Move only",          callback_data=f"learn_move:{email_id}:{folder}")],
        [InlineKeyboardButton("📄 Export PDF only",    callback_data=f"learn_ask_path:{email_id}:{folder}")],
        [InlineKeyboardButton("📁 + 📄 Move & Export", callback_data=f"learn_ask_path:{email_id}:{folder}:with_move")],
        [InlineKeyboardButton("Just this once",        callback_data=f"skip_learn:{email_id}")],
    ])

    await query.edit_message_text(
        f"{status_icon} Moved to *{folder}*.\n\n"
        f"Save this as a rule for future emails from this sender?",
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

    # Treat as email search query — delegate to query-worker via Redis
    await _enqueue_query(update, text)


async def _enqueue_query(update: Update, query_text: str):
    """Push a query job to Redis for the query-worker to process."""
    _, settings = get_session_factory()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    job = json.dumps({
        "type": "query",
        "chat_id": str(update.effective_chat.id),
        "tenant_id": 1,
        "query_text": query_text,
    })
    await r.lpush(QUERY_QUEUE_KEY, job)
    await r.aclose()
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
# Admin commands: /status, /recover, /restart
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
        await r.aclose()

        db_lines = "\n".join(f"  {row[0]}: {row[1]}" for row in counts) or "  (no emails)"
        msg = (
            f"📊 *System Status*\n\n"
            f"*DB — Emails by status:*\n{db_lines}\n\n"
            f"*Redis queues:*\n"
            f"  email jobs pending: {email_q}\n"
            f"  query jobs pending: {query_q}"
        )
    except Exception as e:
        msg = f"⚠️ Status check failed: {e}"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_recover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_factory, _ = get_session_factory()

    try:
        async with session_factory() as session:
            from sqlalchemy import text
            result = await session.execute(text(
                "UPDATE emails SET status='new', processed_at=NULL "
                "WHERE status='pending_review' RETURNING id"
            ))
            ids = [str(row[0]) for row in result.all()]
            await session.commit()

        if ids:
            msg = f"♻️ Reset {len(ids)} stuck email(s) to *new*: {', '.join(ids)}\n\nAI worker will reprocess them shortly."
        else:
            msg = "✅ No emails stuck in pending\\_review."
    except Exception as e:
        msg = f"⚠️ Recovery failed: {e}"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, settings = get_session_factory()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    restart_job = json.dumps({"type": "restart"})
    await r.lpush(EMAIL_QUEUE_KEY, restart_job)
    await r.lpush(QUERY_QUEUE_KEY, restart_job)
    await r.aclose()

    await update.message.reply_text(
        "🔄 Restart signal sent to *ai-worker* and *query-worker*.\n"
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
    app.add_handler(CommandHandler("search",  handle_search_command))
    app.add_handler(CallbackQueryHandler(handle_classify,       pattern=r"^classify:"))
    app.add_handler(CallbackQueryHandler(handle_learn_move,     pattern=r"^learn_move:"))
    app.add_handler(CallbackQueryHandler(handle_learn_ask_path, pattern=r"^learn_ask_path:"))
    app.add_handler(CallbackQueryHandler(handle_learn_pdf,      pattern=r"^learn_pdf:"))
    app.add_handler(CallbackQueryHandler(handle_skip_learn,     pattern=r"^skip_learn:"))
    app.add_handler(CallbackQueryHandler(handle_query_show,     pattern=r"^query_show:"))
    app.add_handler(CallbackQueryHandler(handle_query_email,    pattern=r"^query_email:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 Telegram bot started — polling for callbacks...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
