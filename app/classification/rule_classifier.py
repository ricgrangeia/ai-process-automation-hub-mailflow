"""
RuleClassifier — context builder for the LLM classification pipeline.

Builds a context dict describing what the system knows about this sender:
  - sender_history: folders confirmed for this sender + hit counts
  - matched_keywords: inbox filter keywords present in this email

The context is injected into the LLM prompt as memory.
The LLM always makes the final classification decision.
"""

import logging
import os

logger = logging.getLogger("rule-classifier")


class RuleClassifier:

    def __init__(self, session_factory=None):
        self.session_factory = session_factory

    async def get_context(self, email) -> dict:
        """
        Return what the system knows about this sender.

        {
            "sender_history": [{"folder": "Faturas", "hits": 15}, ...],
            "matched_keywords": ["fatura", "pagamento"],
        }

        Never raises — returns empty context on any error.
        """
        context: dict = {"sender_history": [], "matched_keywords": []}

        if not self.session_factory:
            return context

        sender = (email.from_address or "").lower()

        try:
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
            import asyncio as _asyncio
            from app.core.system_settings import get_inbox_keywords
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
