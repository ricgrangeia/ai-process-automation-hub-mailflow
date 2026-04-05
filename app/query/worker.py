"""
Query Worker — processes natural language email search jobs from Redis.

Job types (on QUERY_QUEUE_KEY):

  Search job (pushed by telegram/bot.py):
    {"type": "query", "chat_id": "...", "tenant_id": 1, "query_text": "..."}

  Delivery job (pushed by telegram/bot.py after user picks delivery method):
    {"type": "query_deliver", "result_id": "...", "method": "inline"|"email", "chat_id": "..."}

Flow:
  1. query job  → parse LLM filters → search DB
  2. Store results in Redis (TTL 10 min) under mailai:query:result:{result_id}
  3. Ask user via Telegram inline buttons: Show here / Send by email
  4. User taps button → bot pushes query_deliver job
  5. query_deliver job → fetch results from Redis → deliver
"""

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

import httpx
import redis.asyncio as aioredis

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.core.config import get_settings
from app.core.database.engine import make_engine, make_session_factory
from app.query.queue import QUERY_QUEUE_KEY, RESULT_KEY_PREFIX, RESULT_TTL_SECONDS
from app.query.parser import parse_query
from app.query.repository import search_emails
from app.query.exporter import send_results_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("query-worker")


async def _telegram_send(bot_token: str, chat_id: str, text: str, keyboard=None) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.warning(f"Could not send Telegram message to {chat_id}: {e}")


def _build_inline_summary(emails: list) -> str:
    lines = [f"📬 Found *{len(emails)}* email(s):\n"]
    for i, email in enumerate(emails[:10], 1):
        received = email.received_at.strftime("%Y-%m-%d") if email.received_at else "?"
        lines.append(
            f"{i}. *{(email.subject or '(no subject)')[:60]}*\n"
            f"   {email.from_address or '?'} | {received} | {email.classification_label or '?'}"
        )
    if len(emails) > 10:
        lines.append(f"\n_…and {len(emails) - 10} more._")
    return "\n".join(lines)


async def _handle_search(job: dict, settings, session_factory, r) -> None:
    chat_id = str(job.get("chat_id", ""))
    tenant_id = int(job.get("tenant_id", 1))
    query_text = job.get("query_text", "").strip()

    if not query_text:
        logger.warning("Empty query_text — skipping.")
        return

    logger.info(f"Processing query: '{query_text}' for chat_id={chat_id}")

    filters = await parse_query(query_text, settings)
    if not filters:
        await _telegram_send(
            settings.telegram_bot_token, chat_id,
            "⚠️ Could not understand the query. Try again with more details."
        )
        return

    emails = await search_emails(session_factory, tenant_id=tenant_id, filters=filters)
    if not emails:
        await _telegram_send(
            settings.telegram_bot_token, chat_id,
            "📭 No emails found matching your query."
        )
        return

    # Serialise results to Redis so the delivery job can retrieve them
    result_id = uuid.uuid4().hex
    result_key = f"{RESULT_KEY_PREFIX}{result_id}"
    payload = {
        "emails": [
            {
                "id": email.id,
                "subject": email.subject,
                "from_address": email.from_address,
                "received_at": email.received_at.isoformat() if email.received_at else None,
                "classification_label": email.classification_label,
                "raw_path": email.raw_path,
            }
            for email in emails
        ],
        "filters": filters,
        "query_text": query_text,
        "chat_id": chat_id,
    }
    await r.setex(result_key, RESULT_TTL_SECONDS, json.dumps(payload))

    # Ask the user how they want the results
    keyboard = [
        [
            {"text": "📱 Show here", "callback_data": f"query_show:{result_id}"},
            {"text": "📧 Send by email", "callback_data": f"query_email:{result_id}"},
        ]
    ]
    smtp_note = "" if settings.smtp_host else "\n_(SMTP not configured — only inline available)_"
    await _telegram_send(
        settings.telegram_bot_token, chat_id,
        f"✅ Found *{len(emails)}* email(s) matching your query.\nHow would you like the results?{smtp_note}",
        keyboard=keyboard,
    )


async def _handle_deliver(job: dict, settings, r) -> None:
    result_id = job.get("result_id", "")
    method = job.get("method", "inline")
    chat_id = str(job.get("chat_id", ""))

    result_key = f"{RESULT_KEY_PREFIX}{result_id}"
    raw = await r.get(result_key)
    if not raw:
        await _telegram_send(
            settings.telegram_bot_token, chat_id,
            "⚠️ Results expired. Please search again."
        )
        return

    data = json.loads(raw)
    await r.delete(result_key)

    emails_data = data["emails"]
    filters = data["filters"]
    query_text = data["query_text"]

    if method == "email":
        if not settings.smtp_host:
            await _telegram_send(
                settings.telegram_bot_token, chat_id,
                "⚠️ SMTP is not configured. Showing results here instead.\n\n"
                + _build_inline_summary_from_dicts(emails_data)
            )
            return

        # Re-hydrate lightweight objects for the exporter
        emails_obj = [_EmailProxy(e) for e in emails_data]
        sent = send_results_email(settings, emails_obj, filters, query_text)
        if sent:
            await _telegram_send(
                settings.telegram_bot_token, chat_id,
                f"📧 Results sent to `{settings.report_recipient}`.",
            )
        else:
            await _telegram_send(
                settings.telegram_bot_token, chat_id,
                "⚠️ Failed to send email. Check SMTP settings."
            )
    else:
        await _telegram_send(
            settings.telegram_bot_token, chat_id,
            _build_inline_summary_from_dicts(emails_data),
        )


def _build_inline_summary_from_dicts(emails_data: list) -> str:
    lines = [f"📬 Found *{len(emails_data)}* email(s):\n"]
    for i, e in enumerate(emails_data[:10], 1):
        received = e["received_at"][:10] if e.get("received_at") else "?"
        lines.append(
            f"{i}. *{(e.get('subject') or '(no subject)')[:60]}*\n"
            f"   {e.get('from_address') or '?'} | {received} | {e.get('classification_label') or '?'}"
        )
    if len(emails_data) > 10:
        lines.append(f"\n_…and {len(emails_data) - 10} more._")
    return "\n".join(lines)


class _EmailProxy:
    """Lightweight stand-in so send_results_email can iterate email objects."""
    def __init__(self, d: dict):
        self.id = d.get("id")
        self.subject = d.get("subject")
        self.from_address = d.get("from_address")
        self.classification_label = d.get("classification_label")
        self.raw_path = d.get("raw_path")
        from datetime import datetime
        raw_dt = d.get("received_at")
        self.received_at = datetime.fromisoformat(raw_dt) if raw_dt else None


async def run():
    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    logger.info("🔍 Query worker started — waiting for jobs on %s", QUERY_QUEUE_KEY)

    while True:
        try:
            result = await r.brpop(QUERY_QUEUE_KEY, timeout=5)
            if result is None:
                continue

            _, raw = result
            job = json.loads(raw)
            job_type = job.get("type")

            if job_type == "query":
                await _handle_search(job, settings, session_factory, r)
            elif job_type == "query_deliver":
                await _handle_deliver(job, settings, r)
            else:
                logger.warning(f"Unknown job type: {job_type}")

        except Exception as e:
            logger.error(f"Query worker error: {e}", exc_info=True)
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(run())
