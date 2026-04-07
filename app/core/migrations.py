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

        # Build the path to the alembic/ directory:
        # this file lives at  <project_root>/app/core/migrations.py
        # alembic/            lives at  <project_root>/alembic/
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        alembic_dir = os.path.join(project_root, "alembic")

        # Configure programmatically — no reliance on alembic.ini being
        # discoverable at runtime inside the container.
        cfg = Config()
        cfg.set_main_option("script_location", alembic_dir)
        cfg.set_main_option("sqlalchemy.url", os.environ.get("DATABASE_URL", ""))

        logger.info("🗄️  Running database migrations (alembic upgrade head)...")
        command.upgrade(cfg, "head")
        logger.info("✅ Database migrations complete.")
    except Exception:
        logger.exception("❌ Database migration failed — check alembic config and DATABASE_URL.")
        raise
