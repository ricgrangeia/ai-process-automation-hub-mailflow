"""
Review Worker — sends rich Telegram review cards when Learning Mode is active.

Job format (pushed by ai-worker):
  {
    "type": "review",
    "email_id": 42,
    "folder": "Marketing",
    "confidence": 0.85,
    "source": "llm",
    "sender_name": "LinkedIn",
    "sender_type": "company"
  }

The worker only sends the Telegram message.
All correction logic (approve / change folder / fix sender) lives in telegram/bot.py.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.core.config import get_settings
from app.core.database.engine import make_engine, make_session_factory
from app.messages.models import EmailMessage
from app.review.queue import REVIEW_QUEUE_KEY
from app.folders.repository import get_active_folder_names
from app.folders.models import DEFAULT_FOLDERS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("review-worker")


def _sender_label(sender_type: str | None, sender_name: str | None) -> str:
    icon = "🏢" if sender_type == "company" else "👤" if sender_type == "person" else "❓"
    name = sender_name or "Unknown"
    return f"{icon} {name}"


def _extract_keywords_simple(subject: str, body: str, max_kw: int = 3) -> list[str]:
    """Lightweight keyword extractor (no NLP — avoids import from bot.py)."""
    import re, unicodedata
    STOPWORDS = {
        "de", "da", "do", "das", "dos", "em", "a", "o", "as", "os", "e", "ou",
        "um", "uma", "para", "com", "por", "que", "se", "no", "na", "ao", "the",
        "and", "for", "fwd", "re", "fw", "is", "in", "on", "to", "of", "at",
    }
    text = f"{subject} {(body or '')[:300]}"
    words = re.findall(r"[a-záàãâéêíóôõúüçA-Z]{4,}", text)
    seen, out = set(), []
    for w in words:
        nf = unicodedata.normalize("NFD", w.lower())
        nf = "".join(c for c in nf if unicodedata.category(c) != "Mn")
        if nf not in STOPWORDS and nf not in seen:
            seen.add(nf)
            out.append(w.lower())
        if len(out) == max_kw:
            break
    return out


async def _send_review_card(bot_token: str, chat_id: str, email, job: dict, folders: list[str]) -> None:
    folder       = job.get("folder", "?")
    confidence   = int(float(job.get("confidence", 0)) * 100)
    sender_label = _sender_label(job.get("sender_type"), job.get("sender_name"))
    source       = job.get("source", "llm")
    subject      = (email.subject or "(no subject)")[:80]
    sender_email = (email.from_address or "?").lower()
    keywords     = _extract_keywords_simple(email.subject or "", email.body_text or "")
    kw_display   = " · ".join(keywords) if keywords else "none detected"

    text = (
        f"📋 Learning Mode Review\n\n"
        f"Subject: {subject}\n"
        f"From: {sender_email}\n"
        f"Sender: {sender_label}\n\n"
        f"🧠 AI Decision: {folder} ({confidence}% · {source})\n"
        f"🔑 Keywords: {kw_display}\n\n"
        f"What should we do?"
    )

    keyboard = [
        [
            {"text": f"✅ Approve → {folder}", "callback_data": f"rv_approve:{email.id}:{folder}"},
        ],
        [
            {"text": "📁 Change folder", "callback_data": f"rv_folder:{email.id}:{folder}"},
            {"text": "👤 Fix sender",    "callback_data": f"rv_sender:{email.id}"},
        ],
        [
            {"text": "➕ New folder", "callback_data": f"folder_new_request:{email.id}"},
        ],
    ]

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": {"inline_keyboard": keyboard},
            })
        logger.info(f"Sent review card for email {email.id}")
    except Exception as e:
        logger.error(f"Failed to send review card for email {email.id}: {e}")


async def run():
    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    logger.info("📋 Review worker started — waiting for jobs on %s", REVIEW_QUEUE_KEY)

    while True:
        try:
            result = await r.brpop(REVIEW_QUEUE_KEY, timeout=5)
            if result is None:
                continue

            _, raw = result
            job = json.loads(raw)

            if job.get("type") == "restart":
                logger.info("🔄 Restart signal received — exiting for Docker to restart.")
                import sys; sys.exit(0)

            if job.get("type") != "review":
                logger.warning(f"Unknown job type: {job.get('type')}")
                continue

            email_id = job["email_id"]

            async with session_factory() as session:
                email = (await session.execute(
                    select(EmailMessage).where(EmailMessage.id == email_id)
                )).scalar_one_or_none()
                folders = await get_active_folder_names(session)

            if not email:
                logger.warning(f"Email {email_id} not found — skipping review card.")
                continue

            await _send_review_card(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                email,
                job,
                folders,
            )

        except Exception as e:
            logger.error(f"Review worker error: {e}", exc_info=True)
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(run())
