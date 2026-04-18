import asyncio
import logging
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database.engine import make_engine, make_session_factory
from app.core.database.init import init_db
from app.core.crypto import decrypt_secret
from app.core.migrations import run_migrations
from app.accounts.models import EmailAccount
from app.messages.models import EmailMessage, Attachment
from app.messages.storage import save_raw_email, save_attachment
from app.processing.queue import enqueue_email_job, enqueue_invoice_job
from app.ingestion.parser import parse_email
from app.ingestion.imap.client import connect_imap, list_unseen_uids, fetch_messages_by_uids, mark_seen
from app.invoice_worker.detector import classify_email as classify_financial, build_keyword_re


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("imap-worker")


# ─────────────────────────────────────────────────────────────────────────────
# Per-account poll
# ─────────────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
async def process_account_once(settings, session_factory, r, acc: EmailAccount, keyword_re=None):

    if acc.provider != "imap":
        return

    if not acc.imap_host or not acc.username:
        logger.warning(f"[account id={acc.id}] Incomplete config. Skipping.")
        return

    managed_by = acc.managed_by or "ai_worker"
    logger.info(f"[{acc.username}] Checking account (managed_by={managed_by})...")

    password = decrypt_secret(settings.master_key, acc.password_encrypted)

    # Redis key that tracks UIDs already inspected and found non-financial.
    # Prevents non-financial emails from blocking the batch every cycle.
    _skip_key = f"mailai:skipped:{acc.id}"

    def _fetch():
        conn = connect_imap(acc.imap_host, acc.imap_port or 993, acc.username, password)
        try:
            all_uids = list_unseen_uids(conn, settings.inbox_folder)
            return all_uids, conn
        except Exception:
            conn.logout()
            raise

    try:
        all_uids, conn = await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.error(f"[{acc.username}] IMAP connection failed: {e}")
        raise

    if not all_uids:
        logger.info(f"[{acc.username}] No new messages.")
        return

    # Filter out UIDs already inspected and found non-financial this session.
    skipped = await r.smembers(_skip_key)
    pending_uids = [uid for uid in all_uids if uid not in skipped]

    if not pending_uids:
        logger.info(f"[{acc.username}] No new messages (all {len(all_uids)} UNSEEN already inspected).")
        return

    batch_uids = pending_uids[:settings.max_unseen_per_cycle]
    logger.info(
        f"[{acc.username}] {len(all_uids)} UNSEEN total, "
        f"{len(pending_uids)} pending, fetching {len(batch_uids)}."
    )

    def _fetch_batch():
        return list(fetch_messages_by_uids(conn, settings.inbox_folder, batch_uids))

    messages = await asyncio.to_thread(_fetch_batch)
    logger.info(f"[{acc.username}] Fetched {len(messages)} message(s).")

    async with session_factory() as session:
        for uid, raw_bytes, uid_for_seen in messages:

            # ── Duplicate guard ───────────────────────────────────────────────
            exists = await session.execute(
                select(EmailMessage.id).where(
                    EmailMessage.account_id == acc.id,
                    EmailMessage.imap_uid == uid,
                )
            )
            if exists.scalar_one_or_none() is not None:
                logger.info(f"[{acc.username}] Skipping duplicate UID {uid}")
                continue

            parsed = parse_email(raw_bytes)

            # ── Invoice-worker accounts: pre-filter non-financial emails ──────
            # Non-financial emails on invoice accounts are left completely
            # untouched (unread, unmoved, not stored) — intentional behaviour.
            if managed_by == "invoice_worker":
                classification = classify_financial(parsed, keyword_re=keyword_re)
                if classification is None:
                    logger.info(
                        f"[{acc.username}] UID {uid} — not financial, leaving untouched"
                    )
                    # Remember this UID so it doesn't block the batch next cycle.
                    await r.sadd(_skip_key, uid)
                    continue  # do NOT mark seen, do NOT store, do NOT enqueue
            else:
                classification = None  # ai-worker: no pre-filter needed

            # ── Store email + attachments ─────────────────────────────────────
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

            raw_path = save_raw_email(
                settings.storage_root, acc.tenant_id, email_row.id, raw_bytes
            )
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
            logger.info(f"[{acc.username}] Stored email id={email_row.id}")

            # ── Route to the correct worker queue ─────────────────────────────
            if managed_by == "invoice_worker":
                await enqueue_invoice_job(r, acc.tenant_id, email_row.id, classification)
                logger.info(
                    f"[{acc.username}] Enqueued invoice job "
                    f"id={email_row.id} ({classification})"
                )
            else:
                await enqueue_email_job(r, acc.tenant_id, email_row.id)
                logger.info(
                    f"[{acc.username}] Enqueued ai-worker job id={email_row.id}"
                )

            # ── Mark seen ─────────────────────────────────────────────────────
            if settings.mark_seen_after_store:
                await asyncio.to_thread(
                    mark_seen, conn, settings.inbox_folder, uid_for_seen
                )
                logger.info(f"[{acc.username}] Marked UID {uid} as seen")

    try:
        conn.logout()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Worker loop — polls ALL active IMAP accounts
# ─────────────────────────────────────────────────────────────────────────────

async def worker_loop():
    settings = get_settings()
    logger.info("IMAP Worker starting...")

    engine = make_engine(settings.database_url)
    await init_db(engine)
    logger.info("Database initialized.")

    session_factory = make_session_factory(engine)
    r = redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("Connected to Redis.")

    while True:
        try:
            logger.info("Polling cycle started.")

            # Load the active keyword list from system_settings once per cycle.
            # This is a sync DB call; run it in a thread to avoid blocking.
            from app.core.system_settings import get_inbox_keywords
            db_url = settings.database_url.replace("+asyncpg", "")
            keywords = await asyncio.to_thread(get_inbox_keywords, db_url)
            keyword_re = build_keyword_re(keywords)
            logger.info(f"Inbox filter: {len(keywords)} keyword(s) active.")

            async with session_factory() as session:
                res = await session.execute(
                    select(EmailAccount).where(
                        EmailAccount.active == True,
                        EmailAccount.provider == "imap",
                        # All accounts — routing is handled per-message above
                    )
                )
                accounts = list(res.scalars().all())

            logger.info(f"Found {len(accounts)} active IMAP account(s).")

            sem = asyncio.Semaphore(10)

            async def _run(acc):
                async with sem:
                    await process_account_once(
                        settings, session_factory, r, acc, keyword_re=keyword_re
                    )

            await asyncio.gather(*[_run(a) for a in accounts])

        except RetryError as e:
            logger.exception(f"Retry failed: {e.last_attempt.exception()}")
        except SQLAlchemyError as e:
            logger.error(f"DB error: {e}")
        except Exception:
            logger.exception("Unexpected error")

        logger.info(f"Sleeping {settings.poll_interval_sec} seconds...\n")
        await asyncio.sleep(settings.poll_interval_sec)


def main():
    run_migrations()
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
