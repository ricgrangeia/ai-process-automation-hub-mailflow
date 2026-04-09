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


def _esc(value: str) -> str:
    """Escape ILIKE wildcard characters so user input is treated as a literal string."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

from app.messages.models import EmailMessage

logger = logging.getLogger("query.repository")


async def search_emails(session_factory, tenant_id: int, filters: dict) -> list[EmailMessage]:
    conditions = [
        EmailMessage.tenant_id == tenant_id,
        EmailMessage.status == "moved",
    ]

    if filters.get("sender_domain"):
        conditions.append(
            EmailMessage.from_address.ilike(f"%@{_esc(filters['sender_domain'])}", escape="\\")
        )

    if filters.get("sender_email"):
        conditions.append(
            EmailMessage.from_address.ilike(_esc(filters["sender_email"]), escape="\\")
        )

    if filters.get("sender_name"):
        conditions.append(
            EmailMessage.sender_name.ilike(f"%{_esc(filters['sender_name'])}%", escape="\\")
        )

    if filters.get("sender_type"):
        # Whitelist — only accept known values
        if filters["sender_type"] in ("individual", "company"):
            conditions.append(
                EmailMessage.sender_type == filters["sender_type"]
            )

    if filters.get("folder"):
        conditions.append(
            EmailMessage.classification_label.ilike(_esc(filters["folder"]), escape="\\")
        )

    if filters.get("date_from"):
        try:
            conditions.append(
                EmailMessage.received_at >= datetime.fromisoformat(filters["date_from"])
            )
        except ValueError:
            logger.warning(f"Invalid date_from value ignored: {filters['date_from']!r}")

    if filters.get("date_to"):
        try:
            date_to = datetime.fromisoformat(filters["date_to"]).replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
            conditions.append(EmailMessage.received_at <= date_to)
        except ValueError:
            logger.warning(f"Invalid date_to value ignored: {filters['date_to']!r}")

    if filters.get("keyword"):
        kw = _esc(filters["keyword"].lower())
        conditions.append(
            or_(
                EmailMessage.subject.ilike(f"%{kw}%", escape="\\"),
                EmailMessage.body_text.ilike(f"%{kw}%", escape="\\"),
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
