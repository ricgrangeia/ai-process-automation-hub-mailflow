import asyncio
import logging
from datetime import datetime
from sqlalchemy import select

import redis.asyncio as redis

from app.core.config import get_settings
from app.core.database.engine import make_engine, make_session_factory
from app.core.database.init import init_db
from app.core.crypto import decrypt_secret
from app.accounts.models import EmailAccount, ApiCredential
from app.messages.models import EmailMessage
from app.messages.storage import save_raw_email
from app.processing.queue import enqueue_email_job
from app.ingestion.outlook.client import get_app_token, list_unread_inbox_messages, mark_message_read

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api-worker")


def _safe_get(d: dict, path: list[str], default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _parse_graph_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    # Graph returns ISO 8601 like: 2026-02-25T21:10:00Z
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


async def process_outlook_account_once(settings, session_factory, r, acc: EmailAccount):
    # Load tenant Outlook credentials
    async with session_factory() as session:
        cred_res = await session.execute(
            select(ApiCredential).where(
                ApiCredential.tenant_id == acc.tenant_id,
                ApiCredential.provider == "outlook",
                ApiCredential.active == True,
            )
        )
        cred = cred_res.scalar_one_or_none()

    if not cred:
        logger.warning(f"[tenant={acc.tenant_id}] No Outlook credentials found. Skipping.")
        return

    client_secret = decrypt_secret(settings.master_key, cred.client_secret_encrypted)

    token = await get_app_token(
        azure_tenant_id=cred.azure_tenant_id,
        client_id=cred.client_id,
        client_secret=client_secret,
    )

    user = acc.outlook_user or acc.email

    msgs = await list_unread_inbox_messages(token, user=user, top=settings.max_unseen_per_cycle)
    if not msgs:
        logger.info(f"[{user}] No unread Outlook messages.")
        return

    logger.info(f"[{user}] Fetched {len(msgs)} unread Outlook messages.")

    async with session_factory() as session:
        for m in msgs:
            graph_id = m.get("id")
            internet_id = m.get("internetMessageId")
            subject = m.get("subject")
            received_at = _parse_graph_datetime(m.get("receivedDateTime"))

            from_name = _safe_get(m, ["from", "emailAddress", "name"])
            from_addr = _safe_get(m, ["from", "emailAddress", "address"])

            body = m.get("body") or {}
            body_type = (body.get("contentType") or "").lower()
            body_content = body.get("content") or ""
            body_preview = m.get("bodyPreview")

            # Dedup by account_id + imap_uid (we reuse it for Graph id)
            exists = await session.execute(
                select(EmailMessage.id).where(
                    EmailMessage.account_id == acc.id,
                    EmailMessage.imap_uid == graph_id,
                )
            )
            if exists.scalar_one_or_none() is not None:
                continue

            email_row = EmailMessage(
                tenant_id=acc.tenant_id,
                account_id=acc.id,
                message_id=internet_id,
                imap_uid=graph_id,
                from_name=from_name,
                from_address=from_addr,
                subject=subject,
                body_text=(body_preview if body_type != "text" else body_content),
                body_html=(body_content if body_type == "html" else None),
                received_at=received_at,
                status="new",
            )

            session.add(email_row)
            await session.flush()

            # Save raw as JSON-ish bytes (good enough for now, later we can store full MIME)
            raw_bytes = str(m).encode("utf-8")
            raw_path = save_raw_email(settings.storage_root, acc.tenant_id, email_row.id, raw_bytes)
            email_row.raw_path = raw_path

            await session.commit()

            logger.info(f"[{user}] Stored Outlook email id={email_row.id}")
            await enqueue_email_job(r, acc.tenant_id, email_row.id)
            logger.info(f"[{user}] Enqueued job for email id={email_row.id}")

            if settings.mark_seen_after_store and graph_id:
                await mark_message_read(token, user=user, message_id=graph_id)


async def api_worker_loop():
    settings = get_settings()

    engine = make_engine(settings.database_url)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    r = redis.from_url(settings.redis_url, decode_responses=True)

    logger.info("API worker started (Outlook/Graph).")

    while True:
        try:
            async with session_factory() as session:
                res = await session.execute(
                    select(EmailAccount).where(
                        EmailAccount.active == True,
                        EmailAccount.provider == "outlook",
                    )
                )
                accounts = list(res.scalars().all())

            if not accounts:
                logger.info("No active Outlook accounts. Sleeping...")
                await asyncio.sleep(settings.poll_interval_sec)
                continue

            sem = asyncio.Semaphore(5)

            async def _run(acc):
                async with sem:
                    await process_outlook_account_once(settings, session_factory, r, acc)

            await asyncio.gather(*[_run(a) for a in accounts])

        except Exception:
            logger.exception("API worker error")

        await asyncio.sleep(settings.poll_interval_sec)


def main():
    asyncio.run(api_worker_loop())


if __name__ == "__main__":
    main()
