import asyncio
import logging
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings
from .db import make_engine, make_session_factory
from .models import EmailAccount, EmailMessage, Attachment
from .mail_parser import parse_email
from .storage import save_raw_email, save_attachment
from .queue import enqueue_email_job
from .imap_worker import fetch_unseen_raw_messages, mark_seen
from .db_init import init_db


# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("email-worker")


# -----------------------------
# Process Account
# -----------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
async def process_account_once(settings, session_factory, r, acc: EmailAccount):

    # 🔥 Safety: Only IMAP accounts
    if acc.provider != "imap":
        return

    if not acc.imap_host:
        logger.warning(f"[{acc.username}] IMAP host missing. Skipping.")
        return

    if not acc.username:
        logger.warning(f"[account id={acc.id}] Username missing. Skipping.")
        return

    logger.info(f"[{acc.username}] Checking account...")

    password = acc.password_encrypted

    def _fetch():
        return list(fetch_unseen_raw_messages(
            host=acc.imap_host,
            port=acc.imap_port or 993,
            username=acc.username,
            password=password,
            folder=settings.inbox_folder,
            max_n=settings.max_unseen_per_cycle
        ))

    try:
        messages = await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.error(f"[{acc.username}] IMAP connection failed: {e}")
        raise

    if not messages:
        logger.info(f"[{acc.username}] No new messages.")
        return

    logger.info(f"[{acc.username}] Fetched {len(messages)} new messages.")

    async with session_factory() as session:
        for uid, raw_bytes, uid_for_seen in messages:

            exists = await session.execute(
                select(EmailMessage.id).where(
                    EmailMessage.account_id == acc.id,
                    EmailMessage.imap_uid == uid
                )
            )

            if exists.scalar_one_or_none() is not None:
                logger.info(f"[{acc.username}] Skipping duplicate UID {uid}")
                continue

            parsed = parse_email(raw_bytes)

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
                settings.storage_root,
                acc.tenant_id,
                email_row.id,
                raw_bytes
            )
            email_row.raw_path = raw_path

            for att in parsed["attachments"]:
                path = save_attachment(
                    settings.storage_root,
                    acc.tenant_id,
                    email_row.id,
                    att["filename"] or "attachment.bin",
                    att["content"]
                )

                session.add(Attachment(
                    email_id=email_row.id,
                    filename=att["filename"],
                    mime_type=att["mime_type"],
                    path=path,
                    sha256=att["sha256"]
                ))

            await session.commit()

            logger.info(f"[{acc.username}] Stored email id={email_row.id}")

            await enqueue_email_job(r, acc.tenant_id, email_row.id)

            logger.info(f"[{acc.username}] Enqueued job for email id={email_row.id}")

            if settings.mark_seen_after_store:
                await asyncio.to_thread(
                    mark_seen,
                    acc.imap_host,
                    acc.imap_port or 993,
                    acc.username,
                    password,
                    settings.inbox_folder,
                    uid_for_seen
                )
                logger.info(f"[{acc.username}] Marked UID {uid} as seen")


# -----------------------------
# Worker Loop
# -----------------------------
async def worker_loop():
    settings = get_settings()
    logger.info("Worker starting...")

    engine = make_engine(settings.database_url)
    await init_db(engine)
    logger.info("Database initialized.")

    session_factory = make_session_factory(engine)
    r = redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("Connected to Redis.")

    while True:
        try:
            logger.info("Polling cycle started.")

            async with session_factory() as session:
                res = await session.execute(
                    select(EmailAccount).where(
                        EmailAccount.active == True,
                        EmailAccount.provider == "imap"  # 🔥 FIX HERE
                    )
                )
                accounts = list(res.scalars().all())

            logger.info(f"Found {len(accounts)} active IMAP accounts.")

            sem = asyncio.Semaphore(10)

            async def _run(acc):
                async with sem:
                    await process_account_once(settings, session_factory, r, acc)

            await asyncio.gather(*[_run(a) for a in accounts])

        except RetryError as e:
            original = e.last_attempt.exception()
            logger.exception(f"Retry failed: {original}")

        except SQLAlchemyError as e:
            logger.error(f"DB error: {e}")

        except Exception:
            logger.exception("Unexpected error")

        logger.info(f"Sleeping {settings.poll_interval_sec} seconds...\n")
        await asyncio.sleep(settings.poll_interval_sec)


def main():
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()