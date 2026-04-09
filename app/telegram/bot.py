"""
Telegram Bot — NeedsReview callback handler.

Callback data formats:
  classify:{email_id}:{folder}            — user picked a folder
  folder_new_request:{email_id}          — user wants to type a new folder name
  folder_suggest_add:{email_id}:{folder} — user approved AI-suggested new folder
  learn_move:{email_id}:{folder}          — save rule: move only
  learn_pdf:{email_id}:{folder}:{path}    — save rule: export PDF only
  learn_both:{email_id}:{folder}:{path}   — save rule: move + export PDF
  learn_ask_path:{email_id}:{folder}:{with_move}:{kw_encoded} — ask for PDF export path
  kw_keep / kw_edit / kw_none             — keywords confirmation step
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

# Stores pending path input per chat: {chat_id: (email_id, folder, with_move, keywords)}
_pending_path: dict[int, tuple] = {}
# Stores pending keywords input per chat: {chat_id: (email_id, folder, with_move, path, keywords)}
_pending_keywords: dict[int, tuple] = {}
# Stores pending new-folder name input per chat: {chat_id: email_id}
_pending_new_folder: dict[int, int] = {}

# ── Rule draft card state ──────────────────────────────────────────────────────
# Single interactive card that the user configures before saving a rule.
# {chat_id: {"email_id", "message_id", "folder", "keywords", "export_path", "sender_email", "qr_info"}}
_rule_draft: dict[int, dict] = {}
# Which field the user is currently typing: "keywords" | "path" | "folder"
_rule_input_mode: dict[int, str] = {}

DEFAULT_PDF_PATH = "Exports/{year}/{month}/"


# ------------------------------------------------------------------------------
# Rule-draft card builder
# ------------------------------------------------------------------------------

def _build_rule_card(draft: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Build the text + keyboard for the single interactive rule-setup card."""
    folder      = draft["folder"]
    keywords    = draft.get("keywords") or []
    path        = draft.get("export_path")
    sender      = draft.get("sender_email", "")
    qr_info     = draft.get("qr_info", "")

    kw_display   = " · ".join(f"`{k}`" for k in keywords) if keywords else "_none_"
    path_display = f"`{path}`" if path else "_not set_"

    text = (
        f"✅ Moved to *{folder}*\n\n"
        f"📋 *Configure rule for future emails:*\n"
        f"📧 `{sender}`\n"
        f"📁 Folder: *{folder}*\n"
        f"🔑 Keywords: {kw_display}\n"
        f"📂 Export path: {path_display}"
        f"{qr_info}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Save rule", callback_data="rd_save")],
        [InlineKeyboardButton("📁 Move only (no rule)",  callback_data="rd_move")],
        [
            InlineKeyboardButton("✏️ Keywords",     callback_data="rd_kw"),
            InlineKeyboardButton("📂 Export path",  callback_data="rd_path"),
        ],
        [InlineKeyboardButton("➕ New folder",       callback_data="rd_newfolder")],
    ])

    return text, keyboard


async def _send_rule_card(query, chat_id: int, email_id: int, folder: str,
                          keywords: list[str], sender_email: str, qr_info: str = ""):
    """Replace the current message with the rule-setup card and persist the draft."""
    draft = {
        "email_id":     email_id,
        "message_id":   query.message.message_id,
        "folder":       folder,
        "keywords":     keywords,
        "export_path":  None,
        "sender_email": sender_email,
        "qr_info":      qr_info,
    }
    text, keyboard = _build_rule_card(draft)
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as _e:
        if "is not modified" not in str(_e):
            raise
    _rule_draft[chat_id] = draft


async def _refresh_rule_card(bot, chat_id: int):
    """Re-render the rule card in-place after a draft field changes."""
    draft = _rule_draft.get(chat_id)
    if not draft:
        return
    text, keyboard = _build_rule_card(draft)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=draft["message_id"],
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    except Exception as _e:
        if "is not modified" not in str(_e):
            raise


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

async def _safe_answer(query) -> None:
    """Answer a callback query, ignoring stale/expired query errors.

    Telegram requires query.answer() within 30s. If the bot restarts with a
    backlog, or a slow handler causes Telegram to re-deliver the callback, the
    query ID will be expired and answer() raises BadRequest. Safe to ignore.
    """
    try:
        await query.answer()
    except Exception:
        pass


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


def _extract_keywords(subject: str, body: str, max_keywords: int = 3) -> list[str]:
    """
    Extract up to max_keywords meaningful words from subject + body.

    Strategy:
    - Combine subject + first 500 chars of body
    - Remove punctuation, lowercase
    - Filter stopwords (PT + EN)
    - Pick longest unique words first (longer = more specific)
    """
    import re
    import unicodedata

    _STOPWORDS = {
        # PT
        "de", "da", "do", "das", "dos", "a", "o", "as", "os", "e", "em", "para",
        "por", "com", "se", "no", "na", "nos", "nas", "ao", "à", "um", "uma",
        "que", "este", "esta", "estes", "estas", "esse", "essa", "seu", "sua",
        "mais", "mas", "ou", "não", "é", "foi", "ser", "ter", "tem", "seu",
        "pelo", "pela", "pelos", "pelas", "como", "são", "até", "já", "nos",
        # EN
        "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of",
        "is", "it", "be", "as", "by", "we", "you", "this", "that", "with",
        "from", "have", "has", "are", "was", "were", "will", "your", "our",
    }

    text = f"{subject} {body[:500]}"
    # Normalize accents
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Remove non-alpha (keep spaces)
    text = re.sub(r"[^a-zA-Z\s]", " ", text)
    words = [w.lower() for w in text.split() if len(w) >= 4]

    seen: set[str] = set()
    candidates: list[str] = []
    for w in sorted(words, key=len, reverse=True):
        if w not in _STOPWORDS and w not in seen:
            seen.add(w)
            candidates.append(w)
        if len(candidates) == max_keywords:
            break

    return candidates


async def _save_rule(
    session_factory,
    email,
    folder: str,
    actions: list,
    conditions: list | None = None,
    min_match: int = 1,
) -> str | None:
    """
    Persist a LearnedRule with structured conditions.

    conditions: list of {"type": "sender_email"|"sender_domain"|"keyword", "value": "..."}
    If conditions is None, falls back to sender_email condition only.
    Returns the primary match value (sender email) or None on failure.
    """
    if not email or not email.from_address:
        return None

    sender = email.from_address.lower()

    if conditions is None:
        conditions = [{"type": "sender_email", "value": sender}]

    async with session_factory() as session:
        # Allow multiple rules per sender — only skip if exact same conditions exist
        existing = await session.execute(
            select(LearnedRule).where(
                LearnedRule.tenant_id == email.tenant_id,
                LearnedRule.active == True,
                LearnedRule.conditions == conditions,
            )
        )
        rule = existing.scalar_one_or_none()

        if rule:
            rule.actions = actions
            rule.min_match = min_match
        else:
            session.add(LearnedRule(
                tenant_id=email.tenant_id,
                conditions=conditions,
                min_match=min_match,
                actions=actions,
                created_from_email_id=email.id,
            ))

        await session.commit()

    logger.info(f"📚 Rule saved: conditions={conditions} min_match={min_match} → {actions}")
    return sender


# ------------------------------------------------------------------------------
# Invoice QR extraction helper (shared across classify / rv_approve / rv_set_folder)
# ------------------------------------------------------------------------------

async def _try_invoice_qr_bot(email, folder: str, email_id: int, settings, session_factory) -> str:
    """Extract invoice QR from PDF attachments when folder matches.
    Returns a human-readable status line to append to the Telegram reply."""
    if not ("invoice" in folder.lower() or "fatura" in folder.lower()):
        return ""
    logger.info(f"[invoice-qr] Email {email_id} → folder '{folder}' — starting QR check")
    if not (email and email.raw_path):
        logger.warning(f"[invoice-qr] Email {email_id} — no raw_path, skipping")
        return "\n\n📎 No PDF attachments (no storage path)."
    if not settings.tool_server_url:
        logger.warning(f"[invoice-qr] Email {email_id} — TOOL_SERVER_URL not configured, skipping")
        return ""
    try:
        from app.invoices.extractor import extract_qr_from_pdf, persist_invoice
        from pathlib import Path as _Path
        att_dir = _Path(email.raw_path).parent / "attachments"
        pdfs = list(att_dir.glob("*.pdf")) if att_dir.exists() else []
        logger.info(f"[invoice-qr] Email {email_id} — found {len(pdfs)} PDF(s) in {att_dir}")
        if not pdfs:
            return "\n\n📎 No PDF attachments found."
        decoded_count = 0
        for pdf in pdfs:
            logger.info(f"[invoice-qr] Email {email_id} — sending {pdf.name} to tool server")
            results = await extract_qr_from_pdf(
                str(pdf), settings.tool_server_url, settings.tool_server_api_key
            )
            logger.info(f"[invoice-qr] Email {email_id} — tool server returned {len(results)} result(s) for {pdf.name}")
            for invoice_data in results:
                await persist_invoice(session_factory, email_id, invoice_data)
                decoded_count += 1
            if results:
                break
        if decoded_count:
            logger.info(f"[invoice-qr] Email {email_id} — ✅ {decoded_count} invoice record(s) saved")
            return f"\n\n📎 PDF found · ✅ QR decoded ({decoded_count} invoice record(s) saved)."
        else:
            logger.info(f"[invoice-qr] Email {email_id} — ❌ PDFs found but no QR code decoded")
            return "\n\n📎 PDF found · ❌ No QR code detected."
    except Exception as _e:
        logger.warning(f"[invoice-qr] Email {email_id} — ⚠️ extraction error: {_e}")
        return "\n\n📎 PDF found · ⚠️ QR extraction error."


# ------------------------------------------------------------------------------
# Handler: classify:{email_id}:{folder}
# ------------------------------------------------------------------------------

async def handle_classify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

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

    qr_info      = await _try_invoice_qr_bot(email, folder, email_id, settings, session_factory) if move_success else ""
    sender_email = (email.from_address or "").lower()
    keywords     = _extract_keywords(email.subject or "", email.body_text or "")
    chat_id      = query.message.chat_id

    if move_success:
        await _send_rule_card(query, chat_id, email_id, folder, keywords, sender_email, qr_info)
    else:
        await query.edit_message_text(f"⚠️ Move to *{folder}* failed.", parse_mode="Markdown")


# ------------------------------------------------------------------------------
# Handler: learn_move:{email_id}:{folder}  — save move-only rule
# ------------------------------------------------------------------------------

async def handle_learn_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    # Format: learn_move:{email_id}:{folder}:{kw1|kw2|kw3}  (keywords optional)
    parts = query.data.split(":", 3)
    email_id = int(parts[1])
    folder = parts[2]
    kw_encoded = parts[3] if len(parts) > 3 else ""
    keywords = [k for k in kw_encoded.split("|") if k] if kw_encoded else []

    session_factory, _ = get_session_factory()

    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()

    actions = [{"type": "move_folder", "folder": folder}]

    # Build conditions: sender_email + keywords
    sender_email = (email.from_address or "").lower() if email else ""
    conditions = [{"type": "sender_email", "value": sender_email}]
    for kw in keywords:
        conditions.append({"type": "keyword", "value": kw.lower()})

    # Fire if: email matches OR at least 2 keywords match
    min_match = 1 if not keywords else 2

    saved = await _save_rule(session_factory, email, folder, actions, conditions, min_match)

    if saved:
        kw_line = f"\n🔑 Keywords: {', '.join(f'`{k}`' for k in keywords)}" if keywords else ""
        await log_audit(
            session_factory,
            actor_type="telegram",
            actor_name=_telegram_actor(query.from_user),
            action="rule.created",
            entity_type="rule",
            entity_id=None,
            tenant_id=getattr(email, "tenant_id", None),
            details={"sender": sender_email, "keywords": keywords, "folder": folder},
        )
        await query.edit_message_text(
            f"📚 Rule saved!\n"
            f"📧 `{saved}`{kw_line}\n"
            f"→ *{folder}*",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text("⚠️ Could not save rule — no sender address found.")


# ------------------------------------------------------------------------------
# Handler: learn_ask_path:{email_id}:{folder}[:with_move]
# Asks user to type a PDF export path or accept the default
# ------------------------------------------------------------------------------

async def handle_learn_ask_path(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    # Format: learn_ask_path:{email_id}:{folder}:{with_move_flag}:{kw_encoded}
    parts = query.data.split(":", 4)
    email_id  = int(parts[1])
    folder    = parts[2]
    with_move = (parts[3] == "1") if len(parts) > 3 else True
    kw_encoded = parts[4] if len(parts) > 4 else ""
    keywords   = [k for k in kw_encoded.split("|") if k] if kw_encoded else []

    chat_id = query.message.chat_id
    # Store state so the next plain text message is treated as path input
    _pending_path[chat_id] = (email_id, folder, with_move, keywords)

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
# "Use default path" button — go to keywords step next
# ------------------------------------------------------------------------------

async def handle_learn_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    parts = query.data.split(":", 4)
    email_id  = int(parts[1])
    folder    = parts[2]
    with_move = parts[3] == "1"
    path      = parts[4]

    chat_id = query.message.chat_id
    # Retrieve keywords stored when the path step began
    pending = _pending_path.pop(chat_id, None)
    keywords = pending[3] if pending and len(pending) > 3 else []

    await _ask_keywords_step(query, chat_id, email_id, folder, with_move, path, keywords)


# ------------------------------------------------------------------------------
# Keywords confirmation step (shown after path is set)
# ------------------------------------------------------------------------------

async def _ask_keywords_step(msg_or_query, chat_id: int, email_id: int, folder: str,
                              with_move: bool, path: str, keywords: list[str]):
    """Show the detected keywords and let the user keep, remove, or replace them."""
    _pending_keywords[chat_id] = (email_id, folder, with_move, path, keywords)

    kw_display = " · ".join(f"`{k}`" for k in keywords) if keywords else "_none detected_"

    keep_label = f"✅ Keep: {', '.join(keywords)}" if keywords else "✅ No keywords (sender only)"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(keep_label, callback_data="kw_keep")],
        [InlineKeyboardButton("✏️ Type new keywords", callback_data="kw_edit")],
        [InlineKeyboardButton("❌ Remove keywords",   callback_data="kw_none")],
    ])

    text = (
        f"✅ Path: `{path}`\n\n"
        f"🔑 Keywords detected: {kw_display}\n\n"
        f"Keep them, type your own (space-separated), or remove."
    )

    if hasattr(msg_or_query, "edit_message_text"):
        await msg_or_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await msg_or_query.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def handle_kw_keep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Keep the auto-detected keywords as-is."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    pending = _pending_keywords.pop(chat_id, None)
    if not pending:
        await query.edit_message_text("⚠️ Session expired — please start again.")
        return
    email_id, folder, with_move, path, keywords = pending
    await _persist_pdf_rule(query, email_id, folder, with_move, path, keywords)


async def handle_kw_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask the user to type new keywords."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    # Keep state in _pending_keywords, just change the message
    pending = _pending_keywords.get(chat_id)
    if not pending:
        await query.edit_message_text("⚠️ Session expired — please start again.")
        return
    await query.edit_message_text(
        "✏️ Type your keywords (space-separated):\n\nExample: `fatura referencia pagamento`",
        parse_mode="Markdown",
    )


async def handle_kw_none(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save without keywords — match by sender email only."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    pending = _pending_keywords.pop(chat_id, None)
    if not pending:
        await query.edit_message_text("⚠️ Session expired — please start again.")
        return
    email_id, folder, with_move, path, _ = pending
    await _persist_pdf_rule(query, email_id, folder, with_move, path, keywords=[])


async def _persist_pdf_rule(query_or_message, email_id: int, folder: str,
                             with_move: bool, path: str, keywords: list[str] | None = None):
    if keywords is None:
        keywords = []
    session_factory, _ = get_session_factory()

    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()

    actions = []
    if with_move:
        actions.append({"type": "move_folder", "folder": folder})
    actions.append({"type": "export_pdf", "path": path})

    sender_email = (email.from_address or "").lower() if email else ""
    conditions   = [{"type": "sender_email", "value": sender_email}]
    for kw in keywords:
        conditions.append({"type": "keyword", "value": kw.lower()})
    min_match = 1 if not keywords else 2

    saved = await _save_rule(session_factory, email, folder, actions, conditions, min_match)

    label  = "Move & Export PDF" if with_move else "Export PDF"
    kw_line = f"\n🔑 Keywords: {', '.join(f'`{k}`' for k in keywords)}" if keywords else ""
    msg = (
        f"📚 Rule saved — *{label}* for emails from `{saved}`.\n"
        f"PDF path: `{path}`{kw_line}"
        if saved else
        "⚠️ Could not save rule — no sender address found."
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

    # ── Review-card keyword edit ───────────────────────────────────────────────
    if chat_id in _pending_rv_kw:
        email_id, folder, _, message_id = _pending_rv_kw.pop(chat_id)

        if text.strip() == "-":
            new_kw_encoded = ""
            new_keywords   = []
        else:
            new_keywords   = [w.strip().lower() for w in text.replace(",", " ").split() if w.strip()]
            new_kw_encoded = "|".join(new_keywords)

        kw_display   = " · ".join(new_keywords) if new_keywords else "none detected"

        session_factory, _ = get_session_factory()
        async with session_factory() as session:
            email = (await session.execute(
                select(EmailMessage).where(EmailMessage.id == email_id)
            )).scalar_one_or_none()

        sender_label_text = (email.from_address or "?").lower() if email else "?"
        subject = (email.subject or "(no subject)")[:80] if email else ""
        sender_name = getattr(email, "sender_name", None) or "?"
        sender_type = getattr(email, "sender_type", None)
        icon = "🏢" if sender_type == "company" else "👤" if sender_type == "person" else "❓"

        new_text = (
            f"📋 Learning Mode Review\n\n"
            f"Subject: {subject}\n"
            f"From: {sender_label_text}\n"
            f"Sender: {icon} {sender_name}\n\n"
            f"🔑 Keywords: {kw_display}\n\n"
            f"What should we do?"
        )
        new_keyboard = {
            "inline_keyboard": [
                [{"text": f"✅ Approve → {folder}", "callback_data": f"rv_approve:{email_id}:{folder}:{new_kw_encoded}"}],
                [
                    {"text": "📁 Change folder", "callback_data": f"rv_folder:{email_id}:{folder}"},
                    {"text": "👤 Fix sender",    "callback_data": f"rv_sender:{email_id}"},
                ],
                [
                    {"text": "➕ New folder", "callback_data": f"folder_new_request:{email_id}"},
                    {"text": "✏️ Keywords",  "callback_data": f"rv_edit_kw:{email_id}:{folder}:{new_kw_encoded}"},
                   
                ],
            ]
        }
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=new_text,
                reply_markup=new_keyboard,
            )
        except Exception:
            pass
        return

    # ── Rule-draft card input ──────────────────────────────────────────────────
    if chat_id in _rule_input_mode:
        mode  = _rule_input_mode.pop(chat_id)
        draft = _rule_draft.get(chat_id)

        if not draft:
            await update.message.reply_text("⚠️ Session expired — please start again.")
            return

        if mode == "keywords":
            if text.strip() == "-":
                draft["keywords"] = []
            else:
                draft["keywords"] = [
                    w.strip().lower() for w in text.replace(",", " ").split() if w.strip()
                ]
            await _refresh_rule_card(context.bot, chat_id)
            return

        if mode == "path":
            draft["export_path"] = text.strip()
            await _refresh_rule_card(context.bot, chat_id)
            return

        if mode == "folder":
            folder_name = text.strip()
            if not folder_name:
                await update.message.reply_text("⚠️ Folder name cannot be empty.")
                _rule_input_mode[chat_id] = "folder"  # keep waiting
                return

            session_factory, settings = get_session_factory()

            # Create folder in DB (idempotent)
            async with session_factory() as session:
                from sqlalchemy import text as _sqlt
                existing = await session.execute(
                    _sqlt("SELECT id FROM folders WHERE name = :name"), {"name": folder_name}
                )
                if not existing.scalar_one_or_none():
                    await session.execute(
                        _sqlt("INSERT INTO folders (name, is_active) VALUES (:name, true)"),
                        {"name": folder_name},
                    )
                    await session.commit()

            # Create IMAP folder
            from app.ingestion.imap.client import connect_imap as _connect_imap, ensure_folder_exists as _ensure_folder
            from app.core.crypto import decrypt_secret as _decrypt
            from sqlalchemy import text as _sqlt2
            try:
                async with session_factory() as _sess:
                    _rows = (await _sess.execute(
                        _sqlt2(
                            "SELECT imap_host, imap_port, username, password_encrypted "
                            "FROM email_accounts WHERE active = true AND provider = 'imap'"
                        )
                    )).mappings().all()
                for acc in _rows:
                    try:
                        pw = _decrypt(settings.master_key, acc["password_encrypted"])
                        conn = _connect_imap(acc["imap_host"], int(acc["imap_port"] or 993), acc["username"], pw)
                        _ensure_folder(conn, folder_name)
                        conn.logout()
                    except Exception:
                        pass
            except Exception:
                pass

            # Re-move the email to the new folder
            email_id = draft["email_id"]
            async with session_factory() as session:
                email = (await session.execute(
                    select(EmailMessage).where(EmailMessage.id == email_id)
                )).scalar_one_or_none()
                account = (await session.execute(
                    select(EmailAccount).where(EmailAccount.id == email.account_id)
                )).scalar_one_or_none() if email else None
            if email and account:
                await _do_move(settings, email, account, folder_name)
                async with session_factory() as session:
                    await session.execute(
                        sa_update(EmailMessage)
                        .where(EmailMessage.id == email_id)
                        .values(classification_label=folder_name)
                    )
                    await session.commit()

            draft["folder"] = folder_name
            await _refresh_rule_card(context.bot, chat_id)
            return

    # Keywords input (typed after path is confirmed)
    if chat_id in _pending_keywords:
        email_id, folder, with_move, path, _ = _pending_keywords.pop(chat_id)
        new_keywords = [w.strip().lower() for w in text.replace(",", " ").split() if w.strip()]
        await _persist_pdf_rule(update.message, email_id, folder, with_move, path, keywords=new_keywords)
        return

    # PDF path input
    if chat_id in _pending_path:
        email_id, folder, with_move, keywords = _pending_path.pop(chat_id)
        await _ask_keywords_step(update.message, chat_id, email_id, folder, with_move, text, keywords)
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
        from sqlalchemy import text as _text2

        imap_results = []
        try:
            async with session_factory() as _sess:
                _rows = (await _sess.execute(
                    _text2(
                        "SELECT id, imap_host, imap_port, username, password_encrypted "
                        "FROM email_accounts WHERE active = true AND provider = 'imap'"
                    )
                )).mappings().all()

            for acc in _rows:
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
    await _safe_answer(query)
    result_id = query.data.split(":", 1)[1]
    await _push_delivery_job(query, result_id, "inline")
    await query.edit_message_text("📱 Fetching results…")


async def handle_query_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)
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
    await _safe_answer(query)
    # Format: rv_approve:{email_id}:{folder}[:{kw_encoded}]
    parts      = query.data.split(":", 3)
    email_id   = int(parts[1])
    folder     = parts[2]
    kw_encoded = parts[3] if len(parts) > 3 else ""

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
    qr_info = await _try_invoice_qr_bot(email, folder, email_id, settings, session_factory)

    sender_email = (email.from_address or "").lower() if email else ""
    # Use keywords from the review card (user may have edited them) or fall back to auto-detect
    keywords = [k for k in kw_encoded.split("|") if k] if kw_encoded else \
               (_extract_keywords(email.subject or "", email.body_text or "") if email else [])
    chat_id  = query.message.chat_id
    await _send_rule_card(query, chat_id, email_id, folder, keywords, sender_email, qr_info)


async def handle_rv_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)
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
    await _safe_answer(query)
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

    session_factory2, settings2 = get_session_factory()
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
    qr_info      = await _try_invoice_qr_bot(email, folder, email_id, settings2, session_factory2)
    sender_email = (email.from_address or "").lower()
    keywords     = _extract_keywords(email.subject or "", email.body_text or "")
    chat_id      = query.message.chat_id
    await _send_rule_card(query, chat_id, email_id, folder, keywords, sender_email, qr_info)


async def handle_rv_save_rule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for old-format rv_save_rule buttons still in Telegram chat history."""
    query = update.callback_query
    await _safe_answer(query)
    # Rebuild a rule card from the callback data so the user can use the new flow
    parts      = query.data.split(":", 3)
    email_id   = int(parts[1])
    folder     = parts[2]
    kw_encoded = parts[3] if len(parts) > 3 else ""
    keywords   = [k for k in kw_encoded.split("|") if k] if kw_encoded else []
    chat_id    = query.message.chat_id

    session_factory, _ = get_session_factory()
    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()

    sender_email = (email.from_address or "").lower() if email else ""
    await _send_rule_card(query, chat_id, email_id, folder, keywords, sender_email)


async def handle_rv_skip_rule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for old-format rv_skip_rule buttons still in Telegram chat history."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    _rule_draft.pop(chat_id, None)
    _rule_input_mode.pop(chat_id, None)
    await query.edit_message_text("👍 Done — no rule saved.")


# {chat_id: (email_id, folder, kw_encoded, message_id)}
_pending_rv_kw: dict[int, tuple] = {}


async def handle_rv_edit_kw(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """✏️ Keywords on the review card — ask user to type new keywords."""
    query = update.callback_query
    await _safe_answer(query)
    # Format: rv_edit_kw:{email_id}:{folder}:{kw_encoded}
    parts      = query.data.split(":", 3)
    email_id   = int(parts[1])
    folder     = parts[2]
    kw_encoded = parts[3] if len(parts) > 3 else ""
    chat_id    = query.message.chat_id

    _pending_rv_kw[chat_id] = (email_id, folder, kw_encoded, query.message.message_id)
    kw_display = " · ".join(kw_encoded.split("|")) if kw_encoded else "_none_"

    await query.edit_message_text(
        f"✏️ *Edit keywords*\n\nCurrent: {kw_display}\n\n"
        f"Type new keywords (space or comma separated), or send `-` to clear.",
        parse_mode="Markdown",
    )


async def handle_rv_sender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)
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
    await _safe_answer(query)
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
# Handlers: rule-draft card  (rd_save / rd_move / rd_kw / rd_path /
#           rd_newfolder / rd_default_path)
# ------------------------------------------------------------------------------

async def handle_rd_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm & Save rule — persist rule with all configured options."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    draft = _rule_draft.pop(chat_id, None)

    if not draft:
        await query.edit_message_text("⚠️ Session expired — please start again.")
        return

    email_id   = draft["email_id"]
    folder     = draft["folder"]
    keywords   = draft.get("keywords") or []
    path       = draft.get("export_path")
    session_factory, _ = get_session_factory()

    async with session_factory() as session:
        email = (await session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id)
        )).scalar_one_or_none()

    actions = [{"type": "move_folder", "folder": folder}]
    if path:
        actions.append({"type": "export_pdf", "path": path})

    sender_email = (email.from_address or "").lower() if email else ""
    conditions   = [{"type": "sender_email", "value": sender_email}]
    for kw in keywords:
        conditions.append({"type": "keyword", "value": kw.lower()})
    min_match = 1 if not keywords else 2

    saved = await _save_rule(session_factory, email, folder, actions, conditions, min_match)

    if saved:
        kw_line   = f"\n🔑 Keywords: {', '.join(f'`{k}`' for k in keywords)}" if keywords else ""
        path_line = f"\n📂 Export: `{path}`" if path else ""
        await log_audit(
            session_factory,
            actor_type="telegram",
            actor_name=_telegram_actor(query.from_user),
            action="rule.created",
            entity_type="rule",
            entity_id=None,
            tenant_id=getattr(email, "tenant_id", None),
            details={"sender": sender_email, "keywords": keywords, "folder": folder, "path": path},
        )
        await query.edit_message_text(
            f"📚 Rule saved!\n📧 `{saved}`{kw_line}{path_line}\n→ *{folder}*",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text("⚠️ Could not save rule — no sender address found.")


async def handle_rd_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Move only — dismiss the rule card without saving a rule."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    draft = _rule_draft.pop(chat_id, None)
    folder = draft["folder"] if draft else "folder"
    await query.edit_message_text(f"👍 Moved to *{folder}* — no rule saved.", parse_mode="Markdown")


async def handle_rd_kw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt the user to type new keywords."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    if chat_id not in _rule_draft:
        await query.answer("Session expired.", show_alert=True)
        return
    _rule_input_mode[chat_id] = "keywords"
    draft = _rule_draft[chat_id]
    kw_display = " · ".join(f"`{k}`" for k in (draft.get("keywords") or [])) or "_none_"
    await query.edit_message_text(
        f"✏️ *Edit keywords*\n\nCurrent: {kw_display}\n\n"
        f"Type new keywords (space or comma separated), or send `-` to clear.",
        parse_mode="Markdown",
    )


async def handle_rd_path(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt the user to type an export path."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    if chat_id not in _rule_draft:
        await query.answer("Session expired.", show_alert=True)
        return
    _rule_input_mode[chat_id] = "path"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"Use default: {DEFAULT_PDF_PATH}",
            callback_data="rd_default_path"
        )]
    ])
    await query.edit_message_text(
        f"📂 *Set export path*\n\n"
        f"Type the path or use the default.\n"
        f"Supported variables: `{{year}}`, `{{month}}`, `{{day}}`\n\n"
        f"Example: `Company/{{year}}/{{month}}/Invoices/`",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_rd_default_path(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Use the default export path."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    _rule_input_mode.pop(chat_id, None)
    draft = _rule_draft.get(chat_id)
    if not draft:
        await query.answer("Session expired.", show_alert=True)
        return
    draft["export_path"] = DEFAULT_PDF_PATH
    await _refresh_rule_card(context.bot, chat_id)


async def handle_rd_newfolder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt the user to type a new folder name."""
    query = update.callback_query
    await _safe_answer(query)
    chat_id = query.message.chat_id
    if chat_id not in _rule_draft:
        await query.answer("Session expired.", show_alert=True)
        return
    _rule_input_mode[chat_id] = "folder"
    await query.edit_message_text(
        "➕ *New folder*\n\nType the folder name to create and move the email to:",
        parse_mode="Markdown",
    )


# ------------------------------------------------------------------------------
# Handler: skip_learn:{email_id}
# ------------------------------------------------------------------------------

async def handle_skip_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)
    await query.edit_message_text("👍 Done — no rule saved.")


# ------------------------------------------------------------------------------
# Handler: folder_suggest_add:{email_id}:{folder_name}
# LLM suggested a new folder that doesn't exist yet — user approved adding it.
# ------------------------------------------------------------------------------

async def handle_folder_suggest_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

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
    await _safe_answer(query)

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
    app.add_handler(CallbackQueryHandler(handle_kw_keep,        pattern=r"^kw_keep$"))
    app.add_handler(CallbackQueryHandler(handle_kw_edit,        pattern=r"^kw_edit$"))
    app.add_handler(CallbackQueryHandler(handle_kw_none,        pattern=r"^kw_none$"))
    app.add_handler(CallbackQueryHandler(handle_skip_learn,     pattern=r"^skip_learn:"))
    # Rule-draft card
    app.add_handler(CallbackQueryHandler(handle_rd_save,         pattern=r"^rd_save$"))
    app.add_handler(CallbackQueryHandler(handle_rd_move,         pattern=r"^rd_move$"))
    app.add_handler(CallbackQueryHandler(handle_rd_kw,           pattern=r"^rd_kw$"))
    app.add_handler(CallbackQueryHandler(handle_rd_path,         pattern=r"^rd_path$"))
    app.add_handler(CallbackQueryHandler(handle_rd_default_path, pattern=r"^rd_default_path$"))
    app.add_handler(CallbackQueryHandler(handle_rd_newfolder,    pattern=r"^rd_newfolder$"))
    # Review (learning mode) callbacks
    app.add_handler(CallbackQueryHandler(handle_rv_approve,    pattern=r"^rv_approve:"))
    app.add_handler(CallbackQueryHandler(handle_rv_folder,     pattern=r"^rv_folder:"))
    app.add_handler(CallbackQueryHandler(handle_rv_set_folder, pattern=r"^rv_set_folder:"))
    app.add_handler(CallbackQueryHandler(handle_rv_save_rule,  pattern=r"^rv_save_rule:"))
    app.add_handler(CallbackQueryHandler(handle_rv_skip_rule,  pattern=r"^rv_skip_rule:"))
    app.add_handler(CallbackQueryHandler(handle_rv_edit_kw,    pattern=r"^rv_edit_kw:"))
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
