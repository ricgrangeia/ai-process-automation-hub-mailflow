"""
Query Worker — processes natural language email search jobs from Redis.

Job format pushed by telegram/bot.py:
  {"type": "query", "chat_id": "<telegram_chat_id>", "tenant_id": 1, "query_text": "..."}

Flow:
  1. BLPOP from mailai:jobs:query
  2. parse_query  → structured filters via LLM
  3. search_emails → PostgreSQL results
  4. send_results_email → SMTP (or inline Telegram reply if SMTP not configured)
  5. Notify the originating chat_id via Telegram
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx
import redis.asyncio as redis

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.core.config import get_settings
from app.core.database.engine import make_engine, make_session_factory
from app.query.queue import QUERY_QUEUE_KEY
from app.query.parser import parse_query
from app.query.repository import search_emails
from app.query.exporter import send_results_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("query-worker")


async def _telegram_reply(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
            })
    except Exception as e:
        logger.warning(f"Could not send Telegram reply to {chat_id}: {e}")


async def _process_job(job: dict, settings, session_factory) -> None:
    chat_id = str(job.get("chat_id", ""))
    tenant_id = int(job.get("tenant_id", 1))
    query_text = job.get("query_text", "").strip()

    if not query_text:
        logger.warning("Received query job with empty query_text — skipping.")
        return

    logger.info(f"Processing query: '{query_text}' for chat_id={chat_id}")

    filters = await parse_query(query_text, settings)

    if not filters:
        await _telegram_reply(
            settings.telegram_bot_token, chat_id,
            "⚠️ Could not understand the query. Try again with more details."
        )
        return

    emails = await search_emails(session_factory, tenant_id=tenant_id, filters=filters)

    if not emails:
        await _telegram_reply(
            settings.telegram_bot_token, chat_id,
            "📭 No emails found matching your query."
        )
        return

    sent = send_results_email(settings, emails, filters, query_text)

    if sent:
        await _telegram_reply(
            settings.telegram_bot_token, chat_id,
            f"✅ Found *{len(emails)}* email(s). Results sent to `{settings.report_recipient}`.",
        )
    else:
        # SMTP not configured — send summary directly in chat
        lines = [f"📬 Found *{len(emails)}* email(s):\n"]
        for i, email in enumerate(emails[:10], 1):
            received = email.received_at.strftime("%Y-%m-%d") if email.received_at else "?"
            lines.append(
                f"{i}. *{(email.subject or '(no subject)')[:60]}*\n"
                f"   From: {email.from_address or '?'} | {received} | {email.classification_label or '?'}"
            )
        if len(emails) > 10:
            lines.append(f"\n_…and {len(emails) - 10} more. Configure SMTP to receive full results._")
        await _telegram_reply(
            settings.telegram_bot_token, chat_id,
            "\n".join(lines),
        )


async def run():
    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    r = redis.from_url(settings.redis_url, decode_responses=True)

    logger.info("🔍 Query worker started — waiting for jobs on %s", QUERY_QUEUE_KEY)

    while True:
        try:
            result = await r.brpop(QUERY_QUEUE_KEY, timeout=5)
            if result is None:
                continue

            _, raw = result
            job = json.loads(raw)
            await _process_job(job, settings, session_factory)

        except Exception as e:
            logger.error(f"Query worker error: {e}", exc_info=True)
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(run())
