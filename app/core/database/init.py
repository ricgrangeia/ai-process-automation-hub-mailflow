from sqlalchemy.ext.asyncio import AsyncEngine

# Import Base with all models registered — accounts and messages must be imported
# before create_all so SQLAlchemy knows about every table.
from app.core.database.base import Base
import app.accounts.models  # noqa: F401 — registers EmailAccount, ApiCredential
import app.messages.models  # noqa: F401 — registers EmailMessage, Attachment


async def init_db(engine: AsyncEngine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
