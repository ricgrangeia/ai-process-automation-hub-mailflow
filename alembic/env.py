import os
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Import all models so their metadata is registered on Base.
# Add any new model modules here as the project grows.
# ---------------------------------------------------------------------------
from app.core.database.base import Base  # noqa: F401 — registers DeclarativeBase
import app.messages.models              # noqa: F401 — EmailMessage, Attachment
import app.accounts.models              # noqa: F401 — EmailAccount
import app.classification.learned_rules # noqa: F401 — LearnedRule

# ---------------------------------------------------------------------------
# Alembic Config object, giving access to alembic.ini values.
# ---------------------------------------------------------------------------
config = context.config

# Inject DATABASE_URL from environment (overrides the blank value in alembic.ini).
# Strip "+asyncpg" so we can use the sync engine for offline mode; async engine
# for online mode is built explicitly below.
_raw_url = os.environ.get("DATABASE_URL", "")
if not _raw_url:
    raise RuntimeError("DATABASE_URL environment variable is not set.")
config.set_main_option("sqlalchemy.url", _raw_url)

# Interpret the config file for Python logging, if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline mode — generate SQL script without connecting
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    # Strip asyncpg for offline SQL generation
    url = url.replace("+asyncpg", "")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect and apply migrations
# ---------------------------------------------------------------------------
def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Build async engine from the config URL (includes +asyncpg if present)
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
