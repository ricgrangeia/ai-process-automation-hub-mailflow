"""Run Alembic migrations programmatically at service startup."""
import logging
import os

logger = logging.getLogger(__name__)


def run_migrations() -> None:
    """Apply all pending Alembic migrations (upgrade head).

    Called once at startup by any service that owns schema changes
    (currently: ai-worker). Safe to call from multiple services
    simultaneously — Alembic uses a DB-level lock table.
    """
    try:
        from alembic.config import Config
        from alembic import command

        # Locate alembic.ini relative to this file: project_root/alembic.ini
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        ini_path = os.path.join(project_root, "alembic.ini")

        cfg = Config(ini_path)
        # Ensure DATABASE_URL from environment is picked up (env.py reads it too,
        # but setting it here makes the Config object consistent).
        cfg.set_main_option("sqlalchemy.url", os.environ.get("DATABASE_URL", ""))

        logger.info("🗄️  Running database migrations (alembic upgrade head)...")
        command.upgrade(cfg, "head")
        logger.info("✅ Database migrations complete.")
    except Exception:
        logger.exception("❌ Database migration failed — check alembic config and DATABASE_URL.")
        raise
