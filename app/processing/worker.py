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
from app.telegram.notifications import send_review_request, send_worker_started, send_sender_identification
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
            enriched = {**config, "session_factory": session_factory}
            action = get_action(enriched)
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

async def _try_invoice_qr(email, settings, session_factory) -> None:
    """
    If TOOL_SERVER_URL is configured, try to extract invoice QR data from any
    PDF attachments stored for this email. Runs fire-and-forget; never raises.
    """
    if not getattr(settings, "tool_server_url", None):
        return
    if not email.raw_path:
        return

    from pathlib import Path as _Path
    from app.invoices.service import save_invoice_from_pdf

    att_dir = _Path(email.raw_path).parent / "attachments"
    pdfs = list(att_dir.glob("*.pdf")) if att_dir.exists() else []
    if not pdfs:
        logger.debug(f"No PDF attachments found for email {email.id} — skipping QR extraction")
        return

    for pdf in pdfs:
        result = await save_invoice_from_pdf(session_factory, email.id, pdf, settings)
        if result:
            logger.info(f"Invoice QR extracted for email {email.id} from {pdf.name}")
            break


async def _extract_invoice_for_conflict(email, settings, session_factory) -> dict | None:
    """
    For rule-conflict emails with PDF attachments: try to extract invoice data
    (supplier, number, total, ATCUD) so it can be shown on the Telegram conflict card.

    Returns a dict with the extracted fields, or None if extraction failed / no PDFs.
    Reuses an existing Invoice record if one was already saved for this email.
    """
    try:
        # Check if invoice was already extracted (e.g. by a previous cycle)
        from app.invoices.models import Invoice
        from sqlalchemy import select as _select
        async with session_factory() as s:
            inv = (await s.execute(
                _select(Invoice).where(Invoice.email_id == email.id)
            )).scalar_one_or_none()
        if inv:
            return {
                "supplier_name": inv.supplier_name,
                "invoice_number": inv.invoice_number,
                "document_type_description": inv.document_type_description,
                "total_amount": float(inv.total_amount) if inv.total_amount is not None else None,
                "currency": inv.currency,
                "atcud": inv.atcud,
                "invoice_origin": inv.invoice_origin,
            }

        # No existing record — try to extract from PDF
        if not getattr(settings, "tool_server_url", None) or not email.raw_path:
            return None

        from pathlib import Path as _Path
        from app.invoices.service import save_invoice_from_pdf

        att_dir = _Path(email.raw_path).parent / "attachments"
        pdfs = list(att_dir.glob("*.pdf")) if att_dir.exists() else []
        for pdf in pdfs:
            result = await save_invoice_from_pdf(session_factory, email.id, pdf, settings)
            if result:
                logger.info(f"Invoice extracted for conflict card: email {email.id} from {pdf.name}")
                return result

    except Exception as e:
        logger.warning(f"Invoice extraction for conflict card failed (email {email.id}): {e}")

    return None


async def _find_doc_type_rule_folder(
    session_factory,
    tenant_id: int,
    doc_type_desc: str,
    active_folders: list[str],
) -> str | None:
    """
    Look up an active invoice_document_type rule that matches doc_type_desc.
    Returns the target folder if found and it exists in active_folders, else None.
    """
    from app.classification.learned_rules import LearnedRule

    try:
        async with session_factory() as session:
            rules = (await session.execute(
                select(LearnedRule).where(
                    LearnedRule.active == True,
                    LearnedRule.tenant_id == tenant_id,
                )
            )).scalars().all()

        needle = doc_type_desc.lower()
        for rule in rules:
            for cond in (rule.conditions or []):
                if cond.get("type") == "invoice_document_type" and cond.get("value", "").lower() == needle:
                    target = next(
                        (a["folder"] for a in (rule.actions or []) if a.get("type") == "move_folder"),
                        None,
                    )
                    if target and target in active_folders:
                        return target
    except Exception as e:
        logger.warning(f"Doc-type rule lookup failed: {e}")

    return None


async def _auto_save_rule(session_factory, email, folder: str, confidence: float, settings=None) -> None:
    """
    Auto-save a high-confidence LLM decision as a learned rule.

    Rules always use sender_email + at least one inbox-filter keyword (min_match=2).
    If no configured filter keywords are found in the email, rule creation is skipped
    to avoid creating broad rules that would fire on unrelated emails from the same sender.
    """
    from app.classification.learned_rules import LearnedRule

    if not email.from_address:
        logger.debug(f"Auto-learn: no from_address on email {email.id}, skipping.")
        return

    sender = email.from_address.lower()

    # Require at least one inbox-filter keyword to be present in the email
    keywords: list[str] = []
    if settings:
        try:
            import asyncio as _asyncio
            from app.core.system_settings import get_inbox_keywords
            db_url = settings.database_url.replace("+asyncpg", "")
            filter_kws: list[str] = await _asyncio.to_thread(get_inbox_keywords, db_url)
            text = f"{email.subject or ''} {email.body_text or ''}".lower()
            keywords = [kw.lower() for kw in filter_kws if kw.lower() in text]
        except Exception as e:
            logger.warning(f"Auto-learn: keyword lookup failed: {e}")

    if not keywords:
        logger.info(
            f"Auto-learn: skipping rule for {sender!r} — no inbox filter keywords matched email {email.id}"
        )
        return

    try:
        async with session_factory() as session:
            # Check if any active rule already covers this exact sender email
            all_rules = (await session.execute(
                select(LearnedRule).where(
                    LearnedRule.active == True,
                    LearnedRule.tenant_id == email.tenant_id,
                )
            )).scalars().all()

            for rule in all_rules:
                for cond in (rule.conditions or []):
                    if cond.get("type") == "sender_email" and cond.get("value", "").lower() == sender:
                        logger.debug(f"Auto-learn: rule already exists for {sender}, skipping.")
                        return

            conditions = [{"type": "sender_email", "value": sender}]
            for kw in keywords:
                conditions.append({"type": "keyword", "value": kw})

            new_rule = LearnedRule(
                tenant_id=email.tenant_id,
                conditions=conditions,
                min_match=2,
                actions=[{"type": "move_folder", "folder": folder}],
                created_from_email_id=email.id,
            )
            session.add(new_rule)
            await session.commit()
            logger.info(
                f"🤖 Auto-saved rule: sender_email={sender} + {len(keywords)} keyword(s) → {folder} "
                f"(confidence={confidence:.2f})"
            )

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

            # 3️⃣ Classify — LLM with sender context
            # Rules inform as memory; LLM always makes the final decision.
            op_mode = await get_mode(r)
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
            llm_time = getattr(classification, 'llm_time_seconds', None) or None

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
                            processing_time_seconds=float(llm_time) if llm_time else None,
                        )
                    )
                    await s.commit()
                logger.info(f"📋 Email {email_id} sent to review-worker (learning mode).")
                continue

            # 5️⃣ NeedsReview → delegate to human via Telegram
            if folder == "NeedsReview" and settings.telegram_bot_token and settings.telegram_chat_id:
                # Try to extract invoice data for any NeedsReview email with a PDF —
                # shows ATCUD, supplier, total on the Telegram card so the human has
                # enough information to decide without opening their inbox.
                invoice_info = await _extract_invoice_for_conflict(email, settings, session_factory)

                # Auto-resolve using invoice_document_type rules:
                # If the invoice has a valid ATCUD and an active invoice_document_type
                # rule matches, move directly without asking the human.
                auto_resolved_folder = None
                if invoice_info and invoice_info.get("atcud"):
                    doc_type_desc = invoice_info.get("document_type_description")
                    if doc_type_desc:
                        auto_resolved_folder = await _find_doc_type_rule_folder(
                            session_factory, email.tenant_id, doc_type_desc, active_folders
                        )

                if auto_resolved_folder:
                    folder = auto_resolved_folder
                    logger.info(
                        f"⚡ Auto-resolved rule conflict for email {email_id}: "
                        f"ATCUD={invoice_info['atcud']!r}, "
                        f"Tipo={invoice_info['document_type_description']!r} → {folder}"
                    )
                    # Fall through to normal move path (skip Telegram card)

                else:
                    sent = await send_review_request(
                        bot_token=settings.telegram_bot_token,
                        chat_id=settings.telegram_chat_id,
                        email_id=email_id,
                        subject=email.subject,
                        sender=email.from_address,
                        confidence=confidence,
                        source=source,
                        folders=active_folders,
                        suggested_folder=getattr(classification, 'suggested_folder', None),
                        invoice_info=invoice_info,
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
                                    processing_time_seconds=float(llm_time) if llm_time else None,
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

                    # If sender identity is still unknown after a successful move,
                    # ask via Telegram so the dashboard never shows unknown senders.
                    if (
                        new_status == "moved"
                        and sender_type is None
                        and settings.telegram_bot_token
                        and settings.telegram_chat_id
                    ):
                        await send_sender_identification(
                            bot_token=settings.telegram_bot_token,
                            chat_id=settings.telegram_chat_id,
                            email_id=email_id,
                            subject=email.subject,
                            sender=email.from_address,
                            folder=folder,
                        )
                        logger.info(f"❓ Sent sender-id request for email {email_id}")

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
                    # 7️⃣ Invoice QR extraction (fire and forget, any moved email with PDFs)
                    if new_status == "moved":
                        await _try_invoice_qr(email, settings, session_factory)

                    # 8️⃣ Auto-learn: save high-confidence LLM decisions as rules
                    # source == "llm" covers all context-aware decisions (hybrid/auto_learn)
                    if (
                        op_mode == "auto_learn"
                        and new_status == "moved"
                        and source == "llm"
                        and confidence >= AUTO_LEARN_CONFIDENCE_THRESHOLD
                    ):
                        await _auto_save_rule(session_factory, email, folder, confidence, settings)
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
