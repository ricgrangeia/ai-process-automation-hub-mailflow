from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, Text, DateTime, ForeignKey, func

class Base(DeclarativeBase):
    pass


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


class EmailMessage(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("email_accounts.id"), index=True)

    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # We reuse imap_uid to store either IMAP UID or Outlook Graph message id
    imap_uid: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    from_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)

    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    raw_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id"), index=True)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)