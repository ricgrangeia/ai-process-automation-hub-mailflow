"""
AI Worker - Multi-Tenant Email Processor
- Listen to Redis jobs
- Classify with Hybrid (Rules + Qwen 2.5)
- Move IMAP folders
- Save ROI & Telemetry with high reliability
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import redis.asyncio as redis
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.database.engine import make_engine, make_session_factory
from app.accounts.models import EmailAccount
from app.messages.models import EmailMessage
from app.ingestion.imap.client import connect_imap, move_message
from app.classification.rule_classifier import RuleClassifier
from app.classification.llm_classifier import LLMClassifier
from app.classification.hybrid_classifier import HybridClassifier


# ------------------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ai-worker")


# ------------------------------------------------------------------------------
# Main AI Worker Loop
# ------------------------------------------------------------------------------

async def ai_worker_loop():
    """
    Infinite loop for processing email classification jobs.
    """
    settings = get_settings()

    # Database & Redis setup
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    r = redis.from_url(settings.redis_url, decode_responses=True)

    # Classifier setup
    rule = RuleClassifier()
    llm = LLMClassifier(settings)
    classifier = HybridClassifier(rule, llm, threshold=0.75)

    logger.info("🚀 AI worker is active and waiting for Redis jobs...")

    while True:
        try:
            # 1️⃣ Wait for job from Redis (Blocking BRPOP)
            result = await r.brpop("mailai:jobs:email")
            if not result:
                continue

            _, job_data = result
            job = json.loads(job_data)
            email_id = job["email_id"]

            start_time = time.time()
            logger.info(f"📥 Processing job for Email ID: {email_id}")

            # 2️⃣ Load metadata (Email & Account)
            async with session_factory() as session:
                email_result = await session.execute(
                    select(EmailMessage).where(EmailMessage.id == email_id)
                )
                email = email_result.scalar_one_or_none()

                if not email:
                    logger.warning(f"❌ Email {email_id} not found in DB. Skipping.")
                    continue

                acc_result = await session.execute(
                    select(EmailAccount).where(EmailAccount.id == email.account_id)
                )
                account = acc_result.scalar_one()

                # 3️⃣ Classify email (Intelligence Layer)
                # classification returns .folder, .confidence, .source, .prompt_tokens, etc.
                classification = await classifier.classify(email)

                folder = classification.folder
                confidence = classification.confidence
                source = getattr(classification, 'source', 'llm')

                # 4️⃣ Move email via IMAP (Isolated in Thread)
                imap_password = decrypt_secret(settings.master_key, account.password_encrypted)

                def _move():
                    conn = connect_imap(
                        account.imap_host,
                        account.imap_port or 993,
                        account.username,
                        imap_password
                    )
                    try:
                        move_message(conn, settings.inbox_folder, folder, email.imap_uid)
                        return True
                    except Exception as e:
                        logger.error(f"IMAP Error for email {email_id}: {e}")
                        return False
                    finally:
                        try:
                            conn.logout()
                        except:
                            pass

                move_success = await asyncio.to_thread(_move)

                # 5️⃣ PERSISTENCE: Explicit Database Update
                # We open a dedicated update transaction to avoid row-locking issues
                processing_time = time.time() - start_time
                new_status = "moved" if move_success else "failed_move"

                async with session_factory() as update_session:
                    stmt = (
                        update(EmailMessage)
                        .where(EmailMessage.id == email_id)
                        .values(
                            status=new_status,
                            classification_label=str(folder),
                            ai_confidence=float(confidence),
                            ai_source=str(source),
                            processing_time_seconds=float(processing_time),
                            processed_at=datetime.now(timezone.utc),
                            # ROI Tracking Columns
                            prompt_tokens=getattr(classification, 'prompt_tokens', 0),
                            completion_tokens=getattr(classification, 'completion_tokens', 0),
                            total_tokens=getattr(classification, 'total_tokens', 0)
                        )
                    )

                    result = await update_session.execute(stmt)
                    await update_session.commit()

                    if result.rowcount > 0:
                        logger.info(f"✅ DB Updated: ID {email_id} -> {folder} ({source})")
                    else:
                        logger.error(f"❌ DB Update failed: No row with ID {email_id} was affected.")

        except Exception as e:
            logger.exception(f"🔥 Critical Error in Worker Loop: {e}")
            await asyncio.sleep(5)  # Cooldown on failure


def main():
    """Entrypoint for the worker process."""
    try:
        asyncio.run(ai_worker_loop())
    except KeyboardInterrupt:
        logger.info("👋 Worker stopped by user.")


if __name__ == "__main__":
    main()
