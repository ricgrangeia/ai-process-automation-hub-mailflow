import asyncio
from sqlalchemy import text
from .config import get_settings
from .db import make_engine
from .models import Base

async def wait_for_db(engine):
    print("Waiting for database...")
    while True:
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            print("Database is ready.")
            break
        except Exception:
            await asyncio.sleep(2)

async def run_migrations():
    settings = get_settings()
    engine = make_engine(settings.database_url)

    await wait_for_db(engine)

    print("Creating tables if not exist...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("Schema ensured successfully.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run_migrations())