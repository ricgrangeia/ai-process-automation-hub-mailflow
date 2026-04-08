from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Folder, DEFAULT_FOLDERS


async def get_active_folder_names(session: AsyncSession) -> list[str]:
    """Return names of all active folders ordered alphabetically.
    Falls back to DEFAULT_FOLDERS if the table is empty (first run before migration)."""
    try:
        rows = await session.execute(
            select(Folder.name).where(Folder.is_active == True).order_by(Folder.name)
        )
        names = list(rows.scalars().all())
        return names if names else list(DEFAULT_FOLDERS)
    except Exception:
        return list(DEFAULT_FOLDERS)
