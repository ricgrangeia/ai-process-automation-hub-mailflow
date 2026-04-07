"""
LearnedRule — stores human-confirmed classification decisions.

Each rule has:
  - a match condition (sender_domain, sender_email, subject_contains)
  - a JSON list of actions to execute when the rule fires

Action examples:
  [{"type": "move_folder", "folder": "Invoices"}]
  [{"type": "export_pdf",  "path": "Company/{year}/{month}/Payments/"}]
  [{"type": "move_folder", "folder": "Invoices"},
   {"type": "export_pdf",  "path": "Company/{year}/{month}/Payments/"}]
"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database.base import Base


class LearnedRule(Base):
    __tablename__ = "learned_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)

    # Match condition
    match_field: Mapped[str] = mapped_column(String(32))   # sender_domain | sender_email | subject_contains
    match_value: Mapped[str] = mapped_column(Text)         # "amazon.com"   | "x@y.com"    | "fatura"

    # JSON list of actions — extensible without schema changes
    # e.g. [{"type": "move_folder", "folder": "Invoices"},
    #        {"type": "export_pdf",  "path": "Company/{year}/{month}/Payments/"}]
    actions: Mapped[dict] = mapped_column(JSONB, default=list)

    created_from_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="human")  # "human" | "ai_auto"
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
