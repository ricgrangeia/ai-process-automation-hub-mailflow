from .models import Base
from sqlalchemy.ext.asyncio import AsyncEngine

async def init_db(engine: AsyncEngine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)