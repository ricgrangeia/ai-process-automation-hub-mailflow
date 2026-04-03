from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, Text, DateTime, func

from app.core.database.base import Base


class ApiCredential(Base):
    """
    Stores API credentials per tenant/provider.
    For Outlook/Graph we need:
      - azure_tenant_id (Directory tenant)
      - client_id
      - client_secret (encrypted later; currently encryption disabled in crypto.py)
    """
    __tablename__ = "api_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    tenant_id: Mapped[int] = mapped_column(Integer, index=True)

    # e.g. "outlook"
    provider: Mapped[str] = mapped_column(String(32), index=True)

    azure_tenant_id: Mapped[str] = mapped_column(String(128))
    client_id: Mapped[str] = mapped_column(String(128))
    client_secret_encrypted: Mapped[str] = mapped_column(Text)

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)

    # "imap" (default) or "outlook"
    provider: Mapped[str] = mapped_column(String(16), default="imap", index=True)

    email: Mapped[str] = mapped_column(String(255))

    # IMAP fields (nullable to allow Outlook accounts)
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_port: Mapped[int | None] = mapped_column(Integer, default=993, nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Outlook field (Graph user principal name usually equals email)
    outlook_user: Mapped[str | None] = mapped_column(String(255), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
