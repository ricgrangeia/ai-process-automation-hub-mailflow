"""Add server_default=0 to learned_rules.hit_count.

The column was created with a Python-side default only, so raw SQL INSERTs
that omit hit_count hit a NOT NULL violation.

Revision ID: 017
Revises: 016
Create Date: 2026-04-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "learned_rules",
        "hit_count",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default="0",
    )


def downgrade() -> None:
    op.alter_column(
        "learned_rules",
        "hit_count",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=None,
    )
