from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text

def make_engine(database_url: str):
    # Espera URL tipo: postgresql+asyncpg://user:pass@host:5432/db
    return create_async_engine(database_url, pool_pre_ping=True)

def make_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def healthcheck(session: AsyncSession):
    await session.execute(text("SELECT 1"))
