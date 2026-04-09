from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, Boolean, func, DateTime

from app.core.database.base import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int]           = mapped_column(Integer, primary_key=True)
    name: Mapped[str]         = mapped_column(String(120), nullable=False)
    nif: Mapped[str]          = mapped_column(String(20), nullable=False, unique=True, index=True)
    active: Mapped[bool]      = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
