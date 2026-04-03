from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, Text, DateTime, ForeignKey, func

from app.core.database.base import Base


class EmailMessage(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("email_accounts.id"), index=True)

    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    imap_uid: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    from_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)

    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    raw_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)

    # --- TELEMETRIA IA & PERFORMANCE ---
    classification_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(nullable=True)
    ai_source: Mapped[str | None] = mapped_column(String(32), nullable=True) # 'rule' ou 'llm'
    processing_time_seconds: Mapped[float | None] = mapped_column(nullable=True)
    processed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- NOVOS CAMPOS PARA ROI (DINHEIRO POUPADO) ---
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # ----------------------------------------

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id"), index=True)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
