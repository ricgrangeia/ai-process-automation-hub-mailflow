"""
RuleClassifier

Checks in order:
1. Learned rules from DB (human-confirmed decisions)
   — Priority pass: rules with an invoice_document_type condition are evaluated first.
     These override regular rules when matched.
   — Normal pass: all other rules (sender_email, keyword, etc.)
2. Hardcoded keyword patterns (fast, no DB needed)

Returns None if no rule matches — hybrid_classifier falls through to LLM.

Matching logic for learned rules:
  Each rule has a list of conditions and a min_match threshold.
  Count satisfied conditions → fire if count >= min_match.

  Condition types:
    sender_email           exact match on from_address
    sender_domain          match on domain part of from_address (legacy)
    keyword                word present in subject OR body (case-insensitive)
    invoice_document_type  match on invoice document_type_description (e.g. "Fatura")
                           — requires an Invoice record to exist for the email;
                           — evaluated in a priority pass before normal rules
"""

import logging
from .contracts import ClassificationResult

logger = logging.getLogger("rule-classifier")

# Hardcoded patterns — override with learned rules for more specific matches
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
    # Legacy types — kept for on-the-fly migration of old rules
    if ctype == "subject_contains":
        return value in subject
    if ctype == "body_contains":
        return value in body
    return False


class RuleClassifier:

    def __init__(self, session_factory=None):
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

    async def _get_invoice_doc_type(self, email_id: int) -> str | None:
        """Return document_type_description for the invoice linked to email_id, or None."""
        try:
            from sqlalchemy import select as _sel
            from app.invoices.models import Invoice
            async with self.session_factory() as s:
                inv = (await s.execute(
                    _sel(Invoice.document_type_description).where(Invoice.email_id == email_id)
                )).scalar_one_or_none()
            return inv  # already a str or None
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
                result = await session.execute(
                    select(LearnedRule).where(
                        LearnedRule.active == True,
                        LearnedRule.tenant_id == email.tenant_id,
                    )
                )
                rules = result.scalars().all()

            # Split rules into two passes:
            #   1. Priority — rules that contain at least one invoice_document_type condition
            #   2. Normal   — all other rules
            # Priority rules override normal rules when they match.
            priority_rules = [
                r for r in rules
                if any(c.get("type") == "invoice_document_type" for c in (r.conditions or []))
            ]
            normal_rules = [r for r in rules if r not in priority_rules]

            invoice_doc_type: str | None = None  # lazy-loaded on first need

            for pass_rules in (priority_rules, normal_rules):
                for rule in pass_rules:
                    conditions = rule.conditions or []

                    # Legacy rules: migrate on-the-fly using match_field/match_value
                    if not conditions and rule.match_field and rule.match_value:
                        conditions = [{"type": rule.match_field, "value": rule.match_value}]

                    if not conditions:
                        continue

                    # Lazy-load invoice doc type the first time it's needed
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

                    # Increment hit count
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
            logger.warning(f"Learned rule check failed (falling through to LLM): {e}")

        return None
