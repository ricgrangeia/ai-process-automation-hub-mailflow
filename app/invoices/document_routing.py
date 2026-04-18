"""
Document Type Routing — maps AT document type codes to IMAP folders.

Each code (FT, FR, RG, …) can route to different folders depending on whether
the seller NIF is one of the buyer's own companies (internal) or an external supplier.
"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Boolean, Integer, String, func, DateTime

from app.core.database.base import Base


class DocumentTypeRouting(Base):
    __tablename__ = "document_type_routing"

    id:              Mapped[int]        = mapped_column(Integer, primary_key=True)
    code:            Mapped[str]        = mapped_column(String(10), nullable=False, unique=True, index=True)
    description:     Mapped[str]        = mapped_column(String(100), nullable=False)
    # folder_external: seller is an outside supplier
    folder_external: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # folder_internal: seller NIF matches one of the buyer's own companies
    folder_internal: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active:          Mapped[bool]       = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_at:      Mapped[DateTime]   = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
