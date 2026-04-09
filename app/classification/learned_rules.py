"""
LearnedRule — stores human-confirmed classification decisions.

Each rule has:
  - conditions: JSONB list of match conditions
      [{"type": "sender_email", "value": "invoices@jfl.pt"},
       {"type": "keyword",      "value": "Fatura"},
       {"type": "keyword",      "value": "pagamento"}]
  - min_match: int — how many conditions must be true to fire (default 1)
  - actions: JSONB list of actions to execute when the rule fires
      [{"type": "move_folder", "folder": "Faturas"}]
      [{"type": "export_pdf",  "path": "Company/{year}/{month}/Payments/"}]

Condition types:
  sender_email   — exact match on from_address
  sender_domain  — match on domain part of from_address (legacy, kept for migration)
  keyword        — word present in subject OR body (case-insensitive)

Matching logic:
  count how many conditions are satisfied → fire if count >= min_match
"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database.base import Base


class LearnedRule(Base):
    __tablename__ = "learned_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)

    # New structured conditions — replaces match_field / match_value
    # [{"type": "sender_email"|"sender_domain"|"keyword", "value": "..."}]
    conditions: Mapped[list] = mapped_column(JSONB, default=list)

    # Minimum number of conditions that must match to fire the rule
    min_match: Mapped[int] = mapped_column(Integer, default=1)

    # Legacy columns — kept for backward compatibility, ignored by new matcher
    match_field: Mapped[str | None] = mapped_column(String(32), nullable=True)
    match_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON list of actions
    actions: Mapped[dict] = mapped_column(JSONB, default=list)

    created_from_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="human")  # "human" | "ai_auto"
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
