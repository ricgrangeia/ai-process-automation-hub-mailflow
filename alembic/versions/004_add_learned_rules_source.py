"""Add source column to learned_rules.

Distinguishes human-confirmed rules from AI-auto-saved ones:
  "human"   — created by a human via Telegram review or dashboard
  "ai_auto" — auto-saved by ai-worker in auto_learn mode

Human rules always take precedence and are never overwritten by AI.

Revision ID: 004
Revises: 003
Create Date: 2026-04-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "learned_rules",
        sa.Column("source", sa.String(16), nullable=False, server_default="human"),
    )
    # Existing rules were all created by humans
    op.execute("UPDATE learned_rules SET source = 'human' WHERE source IS NULL")


def downgrade() -> None:
    op.drop_column("learned_rules", "source")
