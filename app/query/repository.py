"""
QueryRepository — executes structured filters against PostgreSQL.

Any filter that is None is simply not applied.
Supports: sender_domain, sender_email, folder (classification_label),
          date_from, date_to, keyword (full-text on subject + body).
"""

import logging
from datetime import datetime, timezone
from sqlalchemy import select, and_, or_, func, cast
from sqlalchemy.types import Text

from app.messages.models import EmailMessage

logger = logging.getLogger("query.repository")


async def search_emails(session_factory, tenant_id: int, filters: dict) -> list[EmailMessage]:
    conditions = [
        EmailMessage.tenant_id == tenant_id,
        EmailMessage.status == "moved",
    ]

    if filters.get("sender_domain"):
        conditions.append(
            EmailMessage.from_address.ilike(f"%@{filters['sender_domain']}")
        )

    if filters.get("sender_email"):
        conditions.append(
            EmailMessage.from_address.ilike(filters["sender_email"])
        )

    if filters.get("folder"):
        conditions.append(
            EmailMessage.classification_label.ilike(filters["folder"])
        )

    if filters.get("date_from"):
        conditions.append(
            EmailMessage.received_at >= datetime.fromisoformat(filters["date_from"])
        )

    if filters.get("date_to"):
        # Include the full last day
        date_to = datetime.fromisoformat(filters["date_to"]).replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        conditions.append(EmailMessage.received_at <= date_to)

    if filters.get("keyword"):
        kw = filters["keyword"].lower()
        conditions.append(
            or_(
                EmailMessage.subject.ilike(f"%{kw}%"),
                EmailMessage.body_text.ilike(f"%{kw}%"),
            )
        )

    async with session_factory() as session:
        result = await session.execute(
            select(EmailMessage)
            .where(and_(*conditions))
            .order_by(EmailMessage.received_at.desc())
            .limit(50)
        )
        emails = result.scalars().all()

    logger.info(f"Query returned {len(emails)} emails for filters: {filters}")
    return list(emails)
