from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, DateTime, func

from app.core.database.base import Base


class Seller(Base):
    __tablename__ = "sellers"

    id:         Mapped[int]          = mapped_column(Integer, primary_key=True)
    nif:        Mapped[str]          = mapped_column(String(20), nullable=False, unique=True, index=True)
    name:       Mapped[str | None]   = mapped_column(String(200), nullable=True)
    activity:   Mapped[str | None]   = mapped_column(String(200), nullable=True)
    cae:        Mapped[str | None]   = mapped_column(String(10),  nullable=True)
    address:    Mapped[str | None]   = mapped_column(String(300), nullable=True)
    situation:  Mapped[str | None]   = mapped_column(String(50),  nullable=True)
    created_at: Mapped[DateTime]     = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime]     = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
