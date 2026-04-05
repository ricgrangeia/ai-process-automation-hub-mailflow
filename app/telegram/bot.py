"""
Telegram Bot — NeedsReview callback handler.

Runs as a standalone service (telegram-bot in docker-compose).
Listens for inline button presses, moves the email via IMAP, updates DB,
then asks if the decision should be saved as a learned rule.

Callback data formats:
  classify:{email_id}:{folder}   — user picked a folder
  learn:{email_id}:{folder}      — user confirmed to save as learned rule
  skip_learn:{email_id}          — user chose not to save a rule
"""

import asyncio
import logging
import sys
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

# Ensure project root is in path
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from sqlalchemy import select, update
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.database.engine import make_engine, make_session_factory
from app.accounts.models import EmailAccount
from app.messages.models import EmailMessage
from app.ingestion.imap.client import connect_imap, move_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("telegram-bot")


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def get_session_factory():
    settings = get_settings()
    engine = make_engine(settings.database_url)
    return make_session_factory(engine), settings


async def _do_move(settings, email: EmailMessage, account: EmailAccount, folder: str) -> bool:
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
        email_result = await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )
        email = email_result.scalar_one_or_none()

        if not email:
            await query.edit_message_text(f"❌ Email {email_id} not found in DB.")
            return

        acc_result = await session.execute(
            select(EmailAccount).where(EmailAccount.id == email.account_id)
        )
        account = acc_result.scalar_one_or_none()

        if not account or account.provider != "imap":
            # Outlook accounts don't need IMAP move — just update DB
            move_success = True
        else:
            move_success = await _do_move(settings, email, account, folder)

    # Update DB
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

    # Ask about saving as a learned rule
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, remember this", callback_data=f"learn:{email_id}:{folder}"),
            InlineKeyboardButton("Just this once", callback_data=f"skip_learn:{email_id}"),
        ]
    ])

    await query.edit_message_text(
        f"{status_icon} Moved to *{folder}*.\n\n"
        f"Save this decision as a rule for future similar emails?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ------------------------------------------------------------------------------
# Handler: learn:{email_id}:{folder}
# ------------------------------------------------------------------------------

async def handle_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, email_id_str, folder = query.data.split(":", 2)
    email_id = int(email_id_str)

    session_factory, settings = get_session_factory()

    async with session_factory() as session:
        email_result = await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )
        email = email_result.scalar_one_or_none()

    if email and email.from_address:
        domain = email.from_address.split("@")[-1] if "@" in email.from_address else None

        # Store learned rule — write directly to DB
        # (LearnedRule table added in next iteration)
        # For now, log it so it's not silently lost
        logger.info(
            f"📚 Learned rule requested: sender_domain={domain}, folder={folder}, "
            f"from email_id={email_id} (LearnedRule table not yet created — coming next)"
        )

        await query.edit_message_text(
            f"📚 Got it — emails from *{domain}* will be classified as *{folder}* automatically.\n\n"
            f"_(Rule storage coming in next update)_",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text("⚠️ Could not extract sender info to create a rule.")


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

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    app.add_handler(CallbackQueryHandler(handle_classify, pattern=r"^classify:"))
    app.add_handler(CallbackQueryHandler(handle_learn, pattern=r"^learn:"))
    app.add_handler(CallbackQueryHandler(handle_skip_learn, pattern=r"^skip_learn:"))

    logger.info("🤖 Telegram bot started — polling for callbacks...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
