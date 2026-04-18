"""
RuleClassifier

Two responsibilities:

1. get_context(email) — primary path for hybrid/auto_learn modes.
   Builds a context dict describing what the system knows about this sender:
   - sender_history: folders this sender has been confirmed into + hit counts
   - matched_keywords: inbox filter keywords present in this email
   The context is passed to the LLM as memory, not as a hard decision.
   The LLM always makes the final call.

2. classify(email) — legacy path used only by rules_only mode.
   Returns a ClassificationResult directly from DB rules.
   Condition types:
     sender_email           exact match on from_address
     sender_domain          match on domain part of from_address (legacy)
     keyword                word present in subject OR body (case-insensitive)
     invoice_document_type  match on invoice document_type_description (priority pass)
"""

import logging
from .contracts import ClassificationResult

logger = logging.getLogger("rule-classifier")

# Hardcoded patterns — used only in rules_only mode
_HARDCODED = [
    (lambda s, b: "invoice" in s or "fatura" in b,   "Invoices",  1.0),
    (lambda s, b: "unsubscribe" in b,                 "Marketing", 1.0),
]


def _condition_matches(
    cond: dict,
    sender: str,
    domain: str,
    subject: str,
    body: str,
    invoice_doc_type: str | None = None,
) -> bool:
    ctype = cond.get("type", "")
    value = (cond.get("value") or "").lower()

    if ctype == "sender_email":
        return sender == value
    if ctype == "sender_domain":
        return domain == value
    if ctype == "keyword":
        return value in subject or value in body
    if ctype == "invoice_document_type":
        if invoice_doc_type is None:
            return False
        return invoice_doc_type.lower() == value
    # Legacy types
    if ctype == "subject_contains":
        return value in subject
    if ctype == "body_contains":
        return value in body
    return False


class RuleClassifier:

    def __init__(self, session_factory=None):
        self.session_factory = session_factory

    # ------------------------------------------------------------------
    # PRIMARY: context builder for LLM-with-memory pipeline
    # ------------------------------------------------------------------

    async def get_context(self, email) -> dict:
        """
        Return what the system knows about this sender as structured context.

        {
            "sender_history": [
                {"folder": "Faturas", "hits": 15},
                ...
            ],
            "matched_keywords": ["fatura", "pagamento"],
        }

        Never raises — returns empty context on any error.
        """
        context = {"sender_history": [], "matched_keywords": []}

        if not self.session_factory:
            return context

        sender = (email.from_address or "").lower()

        try:
            # Build sender history from active rules
            from sqlalchemy import select
            from app.classification.learned_rules import LearnedRule

            async with self.session_factory() as session:
                rules = (await session.execute(
                    select(LearnedRule).where(
                        LearnedRule.active == True,
                        LearnedRule.tenant_id == email.tenant_id,
                    )
                )).scalars().all()

            history: dict[str, int] = {}
            for rule in rules:
                for cond in (rule.conditions or []):
                    if cond.get("type") == "sender_email" and cond.get("value", "").lower() == sender:
                        folder = next(
                            (a["folder"] for a in (rule.actions or []) if a.get("type") == "move_folder"),
                            None,
                        )
                        if folder:
                            history[folder] = history.get(folder, 0) + (rule.hit_count or 0)

            context["sender_history"] = [
                {"folder": f, "hits": h}
                for f, h in sorted(history.items(), key=lambda x: -x[1])
            ]
        except Exception as e:
            logger.debug(f"Context: sender history lookup failed: {e}")

        try:
            # Find which inbox filter keywords appear in this email
            import asyncio as _asyncio
            from app.core.system_settings import get_inbox_keywords
            import os
            db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
            if db_url:
                filter_kws = await _asyncio.to_thread(get_inbox_keywords, db_url)
                text = f"{email.subject or ''} {email.body_text or ''}".lower()
                context["matched_keywords"] = [
                    kw.lower() for kw in filter_kws if kw.lower() in text
                ]
        except Exception as e:
            logger.debug(f"Context: keyword lookup failed: {e}")

        return context

    # ------------------------------------------------------------------
    # LEGACY: direct classifier — used only in rules_only mode
    # ------------------------------------------------------------------

    async def classify(self, email) -> ClassificationResult | None:

        if self.session_factory:
            result = await self._check_learned(email)
            if result:
                return result

        subject = (email.subject or "").lower()
        body = (email.body_text or "").lower()

        for match_fn, folder, confidence in _HARDCODED:
            if match_fn(subject, body):
                result = ClassificationResult(folder, confidence)
                result.source = "rule"
                return result

        return None

    async def _get_invoice_doc_type(self, email_id: int) -> str | None:
        try:
            from sqlalchemy import select as _sel
            from app.invoices.models import Invoice
            async with self.session_factory() as s:
                inv = (await s.execute(
                    _sel(Invoice.document_type_description).where(Invoice.email_id == email_id)
                )).scalar_one_or_none()
            return inv
        except Exception as e:
            logger.debug(f"Invoice doc-type lookup failed for email {email_id}: {e}")
            return None

    async def _check_learned(self, email) -> ClassificationResult | None:
        from sqlalchemy import select
        from app.classification.learned_rules import LearnedRule

        domain = ""
        if email.from_address and "@" in email.from_address:
            domain = email.from_address.split("@")[-1].lower()

        sender  = (email.from_address or "").lower()
        subject = (email.subject or "").lower()
        body    = (email.body_text or "").lower()

        try:
            async with self.session_factory() as session:
                rules = (await session.execute(
                    select(LearnedRule).where(
                        LearnedRule.active == True,
                        LearnedRule.tenant_id == email.tenant_id,
                    )
                )).scalars().all()

            priority_rules = [
                r for r in rules
                if any(c.get("type") == "invoice_document_type" for c in (r.conditions or []))
            ]
            normal_rules = [r for r in rules if r not in priority_rules]

            invoice_doc_type: str | None = None

            for pass_rules in (priority_rules, normal_rules):
                for rule in pass_rules:
                    conditions = rule.conditions or []

                    if not conditions and rule.match_field and rule.match_value:
                        conditions = [{"type": rule.match_field, "value": rule.match_value}]

                    if not conditions:
                        continue

                    needs_invoice = any(c.get("type") == "invoice_document_type" for c in conditions)
                    if needs_invoice and invoice_doc_type is None:
                        invoice_doc_type = await self._get_invoice_doc_type(email.id)

                    min_match = rule.min_match or 1
                    matched_count = sum(
                        1 for c in conditions
                        if _condition_matches(c, sender, domain, subject, body, invoice_doc_type)
                    )

                    if matched_count < min_match:
                        continue

                    async with self.session_factory() as session:
                        db_rule = await session.get(LearnedRule, rule.id)
                        if db_rule:
                            db_rule.hit_count += 1
                            await session.commit()

                    folder = next(
                        (a["folder"] for a in (rule.actions or []) if a.get("type") == "move_folder"),
                        "NeedsReview"
                    )
                    logger.info(
                        f"Learned rule #{rule.id} matched "
                        f"({matched_count}/{len(conditions)} conditions) → {folder}"
                    )
                    result = ClassificationResult(folder, 1.0)
                    result.source = "rule"
                    return result

        except Exception as e:
            logger.warning(f"Learned rule check failed: {e}")

        return None
