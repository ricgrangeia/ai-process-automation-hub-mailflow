"""Run Alembic migrations programmatically at service startup."""
import logging
import os

logger = logging.getLogger(__name__)


def run_migrations() -> None:
    """Apply all pending Alembic migrations (upgrade head).

    Called once at startup by any service that owns schema changes
    (currently: ai-worker). Safe to call from multiple services
    simultaneously — Alembic uses a DB-level lock table.

    After a successful upgrade, the event is written to audit_logs.
    The write is silently skipped if audit_logs doesn't exist yet
    (i.e. before migration 003 has been applied for the first time).
    """
    try:
        from alembic.config import Config
        from alembic import command
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine, text

        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        alembic_dir = os.path.join(project_root, "alembic")
        db_url = os.environ.get("DATABASE_URL", "")

        cfg = Config()
        cfg.set_main_option("script_location", alembic_dir)
        cfg.set_main_option("sqlalchemy.url", db_url)

        # Capture current revision before upgrade so we can detect changes.
        sync_url = db_url.replace("+asyncpg", "")
        sync_engine = create_engine(sync_url)
        with sync_engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            revision_before = ctx.get_current_revision()

        logger.info("🗄️  Running database migrations (alembic upgrade head)...")
        command.upgrade(cfg, "head")

        with sync_engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            revision_after = ctx.get_current_revision()

        logger.info("✅ Database migrations complete (revision: %s).", revision_after)

        # Audit the migration run (silently skipped if audit_logs doesn't exist yet).
        if revision_before != revision_after:
            from app.core.audit import log_audit_sync
            log_audit_sync(
                sync_engine,
                actor_type="system",
                actor_name="alembic",
                action="db.migrated",
                entity_type="system",
                details={
                    "from_revision": revision_before,
                    "to_revision": revision_after,
                },
            )

        sync_engine.dispose()
    except Exception:
        logger.exception("❌ Database migration failed — check alembic config and DATABASE_URL.")
        raise
