from sqlalchemy.ext.asyncio import create_async_engine

def make_engine(database_url: str):
    return create_async_engine(database_url, pool_pre_ping=True)