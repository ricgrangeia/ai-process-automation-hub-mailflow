"""
AI Worker - Multi-Tenant Email Processor
- Listen to Redis jobs
- Classify with Hybrid (Rules + Qwen 2.5)
- Execute actions (move folder, export PDF, etc.)
- Save ROI & Telemetry with high reliability
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

import redis.asyncio as redis
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.database.engine import make_engine, make_session_factory
from app.accounts.models import EmailAccount
from app.messages.models import EmailMessage
from app.classification.rule_classifier import RuleClassifier
from app.classification.llm_classifier import LLMClassifier
from app.classification.hybrid_classifier import HybridClassifier
from app.classification.learned_rules import LearnedRule
from app.processing.queue import QUEUE_KEY
from app.processing.actions.base import get_action
from app.telegram.notifications import send_review_request, send_worker_started
from app.review.queue import REVIEW_QUEUE_KEY, LEARNING_MODE_KEY
from app.core.migrations import run_migrations
from app.core.audit import log_audit
from app.core.operation_mode import (
    get_mode, OPERATION_MODE_KEY, MODES,
    AUTO_LEARN_CONFIDENCE_THRESHOLD, GENERIC_DOMAINS,
)
from app.folders.repository import get_active_folder_names


# ------------------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ai-worker")

MAX_RETRIES = 3


# ------------------------------------------------------------------------------
# Startup Recovery
# ------------------------------------------------------------------------------

async def recover_stuck_emails(r, session_factory, grace_minutes: int = 2):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)

    async with session_factory() as session:
        result = await session.execute(
            select(EmailMessage.id, EmailMessage.tenant_id).where(
                EmailMessage.status == "new",  # pending_review intentionally excluded
                EmailMessage.created_at < cutoff,
            )
        )
        stuck = result.all()

    if not stuck:
        logger.info("✅ No stuck emails found on startup.")
        return

    logger.info(f"♻️  Recovering {len(stuck)} stuck email(s) from before last restart...")
    for email_id, tenant_id in stuck:
        payload = json.dumps({"tenant_id": tenant_id, "email_id": email_id, "type": "process_email", "retries": 0})
        await r.lpush(QUEUE_KEY, payload)
        logger.info(f"   ↩️  Re-queued email id={email_id}")


# ------------------------------------------------------------------------------
# Run actions for a classified email
# Checks learned rules first for any additional actions (e.g. export_pdf),
# then falls back to a plain move_folder if no learned rule is found.
# ------------------------------------------------------------------------------

async def run_actions(email, account, settings, session_factory, folder: str) -> bool:
    # Look for a learned rule that matches this email — it may have richer actions
    actions_config = None

    try:
        domain = None
        if email.from_address and "@" in email.from_address:
            domain = email.from_address.split("@")[-1].lower()
        sender = (email.from_address or "").lower()

        async with session_factory() as session:
            result = await session.execute(
                select(LearnedRule).where(
                    LearnedRule.active == True,
                    LearnedRule.tenant_id == email.tenant_id,
                )
            )
            rules = result.scalars().all()

        for rule in rules:
            matched = False
            if rule.match_field == "sender_domain" and domain:
                matched = domain == rule.match_value.lower()
            elif rule.match_field == "sender_email":
                matched = sender == rule.match_value.lower()
            elif rule.match_field == "subject_contains":
                matched = rule.match_value.lower() in (email.subject or "").lower()
            elif rule.match_field == "body_contains":
                matched = rule.match_value.lower() in (email.body_text or "").lower()

            if matched and rule.actions:
                actions_config = rule.actions
                break

    except Exception as e:
        logger.warning(f"Could not load learned rule actions: {e}")

    # Fall back to plain move_folder if no learned rule found
    if not actions_config:
        actions_config = [{"type": "move_folder", "folder": folder}]

    success = True
    for config in actions_config:
        try:
            action = get_action(config)
            ok = await action.execute(email, account, settings)
            if not ok:
                success = False
                logger.error(f"Action {config.get('type')} failed for email {email.id}")
        except Exception as e:
            logger.error(f"Action {config.get('type')} raised an error for email {email.id}: {e}")
            success = False

    return success


# ------------------------------------------------------------------------------
# Main AI Worker Loop
# ------------------------------------------------------------------------------

async def _auto_save_rule(session_factory, email, folder: str, confidence: float) -> None:
    """
    Auto-save a high-confidence LLM decision as an ai_auto learned rule.
    Skips generic domains, never overwrites human rules.
    """
    from sqlalchemy import select, insert
    from app.classification.learned_rules import LearnedRule

    domain = None
    if email.from_address and "@" in email.from_address:
        domain = email.from_address.split("@")[-1].lower()

    if not domain or domain in GENERIC_DOMAINS:
        logger.debug(f"Auto-learn: skipping generic/missing domain for email {email.id}")
        return

    try:
        async with session_factory() as session:
            # Check if a human rule already exists for this domain+folder combo
            existing = await session.execute(
                select(LearnedRule).where(
                    LearnedRule.active == True,
                    LearnedRule.tenant_id == email.tenant_id,
                    LearnedRule.match_field == "sender_domain",
                    LearnedRule.match_value == domain,
                )
            )
            existing_rule = existing.scalar_one_or_none()

            if existing_rule:
                if existing_rule.source == "human":
                    logger.debug(f"Auto-learn: human rule already exists for {domain}, skipping.")
                    return
                # ai_auto rule exists — update its hit_count but don't duplicate
                existing_rule.hit_count += 1
                await session.commit()
                return

            # Insert new ai_auto rule
            new_rule = LearnedRule(
                tenant_id=email.tenant_id,
                match_field="sender_domain",
                match_value=domain,
                actions=[{"type": "move_folder", "folder": folder}],
                created_from_email_id=email.id,
                source="ai_auto",
            )
            session.add(new_rule)
            await session.commit()
            logger.info(f"🤖 Auto-saved rule: sender_domain={domain} → {folder} (confidence={confidence:.2f})")

    except Exception as e:
        logger.warning(f"Auto-learn rule save failed: {e}")


async def ai_worker_loop():
    settings = get_settings()

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    r = redis.from_url(settings.redis_url, decode_responses=True)

    # Pass session_factory so RuleClassifier can query learned rules
    rule = RuleClassifier(session_factory=session_factory)
    llm = LLMClassifier(settings)
    classifier = HybridClassifier(rule, llm, threshold=0.75)

    logger.info("🚀 AI worker is active and waiting for Redis jobs...")

    await recover_stuck_emails(r, session_factory)

    if settings.telegram_bot_token and settings.telegram_chat_id:
        await send_worker_started(settings.telegram_bot_token, settings.telegram_chat_id)

    while True:
        job = None
        try:
            # 1️⃣ Wait for job from Redis (Blocking BRPOP)
            result = await r.brpop(QUEUE_KEY)
            if not result:
                continue

            _, job_data = result
            job = json.loads(job_data)

            if job.get("type") == "restart":
                logger.info("🔄 Restart signal received — exiting for Docker to restart.")
                import sys; sys.exit(0)

            email_id = job["email_id"]
            retries = job.get("retries", 0)

            logger.info(f"📥 Processing job for Email ID: {email_id} (attempt {retries + 1}/{MAX_RETRIES})")

            # 2️⃣ Load metadata + active folder list
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

                active_folders = await get_active_folder_names(session)

            # 3️⃣ Classify (mode-aware)
            op_mode = await get_mode(r)

            if op_mode == "rules_only":
                classification = await rule.classify(email)
                if not classification:
                    from app.classification.contracts import ClassificationResult
                    classification = ClassificationResult("NeedsReview", 0.0)
                    classification.source = "rules_only_nomatch"
                else:
                    classification.source = "rule"
            elif op_mode == "llm_only":
                classification = await llm.classify(email, folders=active_folders)
                if classification.confidence < 0.75:
                    from app.classification.contracts import ClassificationResult
                    low = ClassificationResult("NeedsReview", classification.confidence)
                    low.source = getattr(classification, 'source', 'llm')
                    low.sender_type = getattr(classification, 'sender_type', None)
                    low.sender_name = getattr(classification, 'sender_name', None)
                    low.prompt_tokens = getattr(classification, 'prompt_tokens', 0)
                    low.completion_tokens = getattr(classification, 'completion_tokens', 0)
                    low.total_tokens = getattr(classification, 'total_tokens', 0)
                    classification = low
            else:  # hybrid or auto_learn
                classification = await classifier.classify(email, folders=active_folders)

            # Validate the returned folder is in the active list.
            # If the LLM suggested an unknown name, preserve it as a suggestion for the human.
            from app.classification.contracts import ClassificationResult as CR
            if classification.folder not in active_folders and classification.folder != "NeedsReview":
                suggested = classification.folder
                logger.info(
                    f"LLM suggested new folder '{suggested}' for email {email_id} "
                    f"— routing to NeedsReview with suggestion"
                )
                unknown = CR("NeedsReview", classification.confidence)
                unknown.source = getattr(classification, 'source', 'llm')
                unknown.sender_type = getattr(classification, 'sender_type', None)
                unknown.sender_name = getattr(classification, 'sender_name', None)
                unknown.prompt_tokens = getattr(classification, 'prompt_tokens', 0)
                unknown.completion_tokens = getattr(classification, 'completion_tokens', 0)
                unknown.total_tokens = getattr(classification, 'total_tokens', 0)
                unknown.suggested_folder = suggested
                classification = unknown

            folder = classification.folder
            confidence = classification.confidence
            source = getattr(classification, 'source', 'llm')
            sender_type = getattr(classification, 'sender_type', None)
            sender_name = getattr(classification, 'sender_name', None)

            # 3b️⃣ Persist sender identity regardless of what happens next
            async with session_factory() as s:
                await s.execute(
                    update(EmailMessage)
                    .where(EmailMessage.id == email_id)
                    .values(sender_type=sender_type, sender_name=sender_name)
                )
                await s.commit()

            # 4️⃣ Learning Mode — route to review queue if not matched by a learned rule
            learning_mode = await r.get(LEARNING_MODE_KEY)
            if learning_mode and source != "rule" and folder != "NeedsReview":
                review_job = json.dumps({
                    "type": "review",
                    "email_id": email_id,
                    "folder": folder,
                    "confidence": confidence,
                    "source": source,
                    "sender_type": sender_type,
                    "sender_name": sender_name,
                })
                await r.lpush(REVIEW_QUEUE_KEY, review_job)
                async with session_factory() as s:
                    await s.execute(
                        update(EmailMessage)
                        .where(EmailMessage.id == email_id)
                        .values(
                            status="pending_review",
                            ai_confidence=float(confidence),
                            ai_source=str(source),
                        )
                    )
                    await s.commit()
                logger.info(f"📋 Email {email_id} sent to review-worker (learning mode).")
                continue

            # 5️⃣ NeedsReview → delegate to human via Telegram
            if folder == "NeedsReview" and settings.telegram_bot_token and settings.telegram_chat_id:
                sent = await send_review_request(
                    bot_token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                    email_id=email_id,
                    subject=email.subject,
                    sender=email.from_address,
                    confidence=confidence,
                    source=source,
                    rule_folder=getattr(classification, 'rule_folder', None),
                    llm_folder=getattr(classification, 'llm_folder', None),
                    folders=active_folders,
                    suggested_folder=getattr(classification, 'suggested_folder', None),
                )
                if sent:
                    async with session_factory() as s:
                        await s.execute(
                            update(EmailMessage)
                            .where(EmailMessage.id == email_id)
                            .values(
                                status="pending_review",
                                ai_confidence=float(confidence),
                                ai_source=str(source),
                                prompt_tokens=getattr(classification, 'prompt_tokens', 0),
                                completion_tokens=getattr(classification, 'completion_tokens', 0),
                                total_tokens=getattr(classification, 'total_tokens', 0),
                            )
                        )
                        await s.commit()
                    logger.info(f"📨 Email {email_id} sent to Telegram for review.")
                    continue

            # 5️⃣ Execute actions (move folder + any learned extras like export_pdf)
            action_success = await run_actions(email, account, settings, session_factory, folder)

            # 6️⃣ Persist result
            new_status = "moved" if action_success else "failed_move"
            llm_time = getattr(classification, 'llm_time_seconds', None)

            async with session_factory() as update_session:
                stmt = (
                    update(EmailMessage)
                    .where(EmailMessage.id == email_id)
                    .values(
                        status=new_status,
                        classification_label=str(folder),
                        ai_confidence=float(confidence),
                        ai_source=str(source),
                        processing_time_seconds=float(llm_time) if llm_time else None,
                        processed_at=datetime.now(timezone.utc),
                        prompt_tokens=getattr(classification, 'prompt_tokens', 0),
                        completion_tokens=getattr(classification, 'completion_tokens', 0),
                        total_tokens=getattr(classification, 'total_tokens', 0),
                        sender_type=sender_type,
                        sender_name=sender_name,
                    )
                )
                result = await update_session.execute(stmt)
                await update_session.commit()

                if result.rowcount > 0:
                    logger.info(f"✅ DB Updated: ID {email_id} -> {folder} ({source})")
                    await log_audit(
                        session_factory,
                        actor_type="system",
                        actor_name="ai-worker",
                        action="email.classified",
                        entity_type="email",
                        entity_id=email_id,
                        tenant_id=email.tenant_id,
                        details={
                            "folder": folder,
                            "source": source,
                            "confidence": round(float(confidence), 4),
                            "status": new_status,
                            "op_mode": op_mode,
                            "sender_type": sender_type,
                            "sender_name": sender_name,
                        },
                    )
                    # 7️⃣ Auto-learn: save high-confidence LLM decisions as rules
                    if (
                        op_mode == "auto_learn"
                        and new_status == "moved"
                        and source != "rule"
                        and confidence >= AUTO_LEARN_CONFIDENCE_THRESHOLD
                    ):
                        await _auto_save_rule(session_factory, email, folder, confidence)
                else:
                    logger.error(f"❌ DB Update failed: No row with ID {email_id} was affected.")

        except Exception as e:
            logger.exception(f"🔥 Critical Error in Worker Loop: {e}")

            if job is not None:
                retries = job.get("retries", 0) + 1
                if retries < MAX_RETRIES:
                    job["retries"] = retries
                    await r.lpush(QUEUE_KEY, json.dumps(job))
                    logger.warning(f"↩️  Re-queued email id={job.get('email_id')} (retry {retries}/{MAX_RETRIES})")
                else:
                    logger.error(f"🚫 Email id={job.get('email_id')} exceeded {MAX_RETRIES} retries — marking as failed_retries")
                    async with session_factory() as s:
                        await s.execute(
                            update(EmailMessage)
                            .where(EmailMessage.id == job["email_id"])
                            .values(status="failed_retries")
                        )
                        await s.commit()

            await asyncio.sleep(5)


def main():
    # Run migrations synchronously before the event loop starts.
    # asyncio.run() cannot be called from within a running loop, so this
    # must happen here rather than inside ai_worker_loop().
    run_migrations()
    try:
        asyncio.run(ai_worker_loop())
    except KeyboardInterrupt:
        logger.info("👋 Worker stopped by user.")


if __name__ == "__main__":
    main()
