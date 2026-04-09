"""Add conditions/min_match to learned_rules; keep legacy columns for migration.

Revision ID: 009
Revises: 008
Create Date: 2026-04-09

Changes:
  - Add conditions JSONB column (new structured match conditions)
  - Add min_match INTEGER column (default 1)
  - Backfill conditions from existing match_field / match_value rows
  - Legacy match_field / match_value columns are kept (nullable) for reference
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns
    op.add_column("learned_rules", sa.Column("conditions", JSONB, nullable=True))
    op.add_column("learned_rules", sa.Column("min_match", sa.Integer(), nullable=True, server_default="1"))

    # Backfill: convert existing match_field/match_value into conditions array
    op.execute("""
        UPDATE learned_rules
        SET conditions = jsonb_build_array(
            jsonb_build_object('type', match_field, 'value', match_value)
        )
        WHERE match_field IS NOT NULL
          AND match_value IS NOT NULL
          AND (conditions IS NULL OR conditions = 'null'::jsonb)
    """)

    # Default empty array for any remaining NULLs
    op.execute("""
        UPDATE learned_rules
        SET conditions = '[]'::jsonb
        WHERE conditions IS NULL
    """)

    # Make match_field / match_value nullable (they are now legacy)
    op.alter_column("learned_rules", "match_field", nullable=True)
    op.alter_column("learned_rules", "match_value", nullable=True)


def downgrade() -> None:
    op.drop_column("learned_rules", "min_match")
    op.drop_column("learned_rules", "conditions")
    op.alter_column("learned_rules", "match_field", nullable=False)
    op.alter_column("learned_rules", "match_value", nullable=False)
