"""
RuleClassifier

Checks in order:
1. Learned rules from DB (human-confirmed decisions)
2. Hardcoded keyword patterns (fast, no DB needed)

Returns None if no rule matches — hybrid_classifier falls through to LLM.
"""

import logging
from .contracts import ClassificationResult

logger = logging.getLogger("rule-classifier")

# Hardcoded patterns — override with learned rules for more specific matches
_HARDCODED = [
    (lambda s, b: "invoice" in s or "fatura" in b,   "Invoices",  1.0),
    (lambda s, b: "unsubscribe" in b,                 "Marketing", 1.0),
]


class RuleClassifier:

    def __init__(self, session_factory=None):
        # session_factory is optional — if None, only hardcoded rules run
        self.session_factory = session_factory

    async def classify(self, email) -> ClassificationResult | None:

        # 1. Learned rules (DB-backed)
        if self.session_factory:
            result = await self._check_learned(email)
            if result:
                return result

        # 2. Hardcoded patterns
        subject = (email.subject or "").lower()
        body = (email.body_text or "").lower()

        for match_fn, folder, confidence in _HARDCODED:
            if match_fn(subject, body):
                result = ClassificationResult(folder, confidence)
                result.source = "rule"
                return result

        return None

    async def _check_learned(self, email) -> ClassificationResult | None:
        from sqlalchemy import select
        from app.classification.learned_rules import LearnedRule

        domain = None
        if email.from_address and "@" in email.from_address:
            domain = email.from_address.split("@")[-1].lower()

        sender = (email.from_address or "").lower()
        subject = (email.subject or "").lower()
        body = (email.body_text or "").lower()

        try:
            async with self.session_factory() as session:
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
                    matched = rule.match_value.lower() in subject
                elif rule.match_field == "body_contains":
                    matched = rule.match_value.lower() in body

                if matched:
                    # Increment hit count
                    async with self.session_factory() as session:
                        db_rule = await session.get(LearnedRule, rule.id)
                        if db_rule:
                            db_rule.hit_count += 1
                            await session.commit()

                    # Return the folder from the first move_folder action
                    folder = next(
                        (a["folder"] for a in (rule.actions or []) if a.get("type") == "move_folder"),
                        "NeedsReview"
                    )
                    logger.info(f"Learned rule matched: {rule.match_field}={rule.match_value} → {folder}")
                    result = ClassificationResult(folder, 1.0)
                    result.source = "rule"
                    return result

        except Exception as e:
            logger.warning(f"Learned rule check failed (falling through to LLM): {e}")

        return None
