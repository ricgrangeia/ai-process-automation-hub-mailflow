"""
AI Worker

Responsible for:
- Listening to Redis queue for new email jobs
- Loading stored emails from database
- Classifying emails using Hybrid classifier (Rules + LLM)
- Moving email to appropriate IMAP folder
- Updating email status in database

Architecture:

Email Worker (store email)  →
Redis Queue                  →
AI Worker (this file)        →
IMAP move + DB update

This worker runs continuously and blocks on Redis BRPOP.
"""

import asyncio
import json
import logging

import redis.asyncio as redis
from sqlalchemy import select

from .config import get_settings
from .db import make_engine, make_session_factory
from .models import EmailMessage, EmailAccount
from .imap_worker import connect_imap, move_message

from .classifier.rule_classifier import RuleClassifier
from .classifier.llm_classifier import LLMClassifier
from .classifier.hybrid_classifier import HybridClassifier


# ------------------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-worker")


# ------------------------------------------------------------------------------
# Main AI Worker Loop
# ------------------------------------------------------------------------------

async def ai_worker_loop():
    """
    Main infinite loop.

    Steps:
    1. Connect to DB and Redis
    2. Wait for email job from Redis
    3. Load email + account from DB
    4. Classify email (Rules + LLM fallback)
    5. Move email in IMAP
    6. Update DB status
    """

    settings = get_settings()

    # Database
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    # Redis (job queue)
    r = redis.from_url(settings.redis_url, decode_responses=True)

    # Classifier setup
    rule = RuleClassifier()
    llm = LLMClassifier(settings)
    classifier = HybridClassifier(rule, llm, threshold=0.75)

    logger.info("AI worker started.")

    while True:

        try:
            # ------------------------------------------------------------------
            # 1️⃣ Wait for job from Redis (blocking)
            # ------------------------------------------------------------------
            # BRPOP blocks until a job is available.
            # Queue format: mailai:jobs:email
            _, job_data = await r.brpop("mailai:jobs:email")

            job = json.loads(job_data)
            email_id = job["email_id"]

            logger.info(f"Processing email job {email_id}")

            # ------------------------------------------------------------------
            # 2️⃣ Load email + account from database
            # ------------------------------------------------------------------
            async with session_factory() as session:

                result = await session.execute(
                    select(EmailMessage).where(EmailMessage.id == email_id)
                )
                email = result.scalar_one_or_none()

                if not email:
                    logger.warning(f"Email {email_id} not found in DB.")
                    continue

                acc_result = await session.execute(
                    select(EmailAccount).where(EmailAccount.id == email.account_id)
                )
                account = acc_result.scalar_one()

                # ------------------------------------------------------------------
                # 3️⃣ Classify email
                # ------------------------------------------------------------------
                classification = await classifier.classify(email)

                folder = classification.folder

                logger.info(
                    f"Email {email_id} → {folder} "
                    f"(confidence={classification.confidence})"
                )

                # ------------------------------------------------------------------
                # 4️⃣ Move email via IMAP
                # ------------------------------------------------------------------
                # IMAP is blocking, so run it in thread to avoid blocking event loop
                def _move():
                    conn = connect_imap(
                        account.imap_host,
                        account.imap_port or 993,
                        account.username,
                        account.password_encrypted
                    )

                    try:
                        move_message(
                            conn,
                            settings.inbox_folder,
                            folder,
                            email.imap_uid
                        )
                    except Exception as e:
                        logger.error(f"Move failed: {e}")    
                    finally:
                        conn.logout()


                await asyncio.to_thread(_move)

                # ------------------------------------------------------------------
                # 5️⃣ Update DB status
                # ------------------------------------------------------------------
                email.status = "moved"
                await session.commit()

                logger.info(f"Email {email_id} moved successfully.")

        except Exception as e:
            # Never let worker crash
            logger.exception(f"AI worker error: {e}")
            await asyncio.sleep(2)


# ------------------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------------------

def main():
    """
    Entrypoint for container execution.
    """
    asyncio.run(ai_worker_loop())


if __name__ == "__main__":
    main()