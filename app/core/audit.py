"""
Audit log helper.

Call log_audit() from any async context to record an auditable event.
Silently skips if the audit_logs table doesn't exist yet (first migration run).

Actor types:
  "system"    — ai-worker, review-worker, query-worker, alembic
  "telegram"  — Telegram user (id + username stored in actor_name)
  "dashboard" — Streamlit dashboard user (env DASHBOARD_USER)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def log_audit(
    session_factory,
    *,
    actor_type: str,           # "system" | "telegram" | "dashboard"
    actor_name: str,           # "@username", "admin", "alembic", "ai-worker", …
    action: str,               # "email.classified", "rule.created", "system.restart", …
    entity_type: str | None = None,   # "email", "rule", "account", "system", "query"
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
    tenant_id: int | None = None,
) -> None:
    """Persist one audit record. Never raises — logs a warning on failure."""
    try:
        from sqlalchemy import text

        row = {
            "actor_type": actor_type,
            "actor_name": actor_name,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": json.dumps(details or {}),
            "tenant_id": tenant_id,
            "created_at": datetime.now(timezone.utc),
        }

        async with session_factory() as session:
            await session.execute(
                text("""
                    INSERT INTO audit_logs
                        (actor_type, actor_name, action, entity_type, entity_id,
                         details, tenant_id, created_at)
                    VALUES
                        (:actor_type, :actor_name, :action, :entity_type, :entity_id,
                         :details, :tenant_id, :created_at)
                """),
                row,
            )
            await session.commit()
    except Exception as exc:
        # Table may not exist yet on first boot — silent skip.
        logger.debug("audit log skipped: %s", exc)


def log_audit_sync(
    engine,
    *,
    actor_type: str,
    actor_name: str,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
    tenant_id: int | None = None,
) -> None:
    """Synchronous variant for use outside async contexts (migrations, dashboard)."""
    try:
        import json as _json
        from sqlalchemy import text

        row = {
            "actor_type": actor_type,
            "actor_name": actor_name,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": _json.dumps(details or {}),
            "tenant_id": tenant_id,
            "created_at": datetime.now(timezone.utc),
        }

        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO audit_logs
                        (actor_type, actor_name, action, entity_type, entity_id,
                         details, tenant_id, created_at)
                    VALUES
                        (:actor_type, :actor_name, :action, :entity_type, :entity_id,
                         :details, :tenant_id, :created_at)
                """),
                row,
            )
            conn.commit()
    except Exception as exc:
        logger.debug("audit log (sync) skipped: %s", exc)


def _telegram_actor(user) -> str:
    """Format a Telegram User object into a readable actor name."""
    if user is None:
        return "telegram:unknown"
    username = f"@{user.username}" if user.username else f"id:{user.id}"
    name = user.full_name or ""
    return f"{name} ({username})" if name else username
