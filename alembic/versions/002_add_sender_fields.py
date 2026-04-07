"""Add sender_name and sender_type to emails table.

sender_name  — TEXT nullable  : human-readable sender name extracted by LLM
               e.g. "Amazon", "João Silva"
sender_type  — VARCHAR(16) nullable : "company" or "person"

Revision ID: 002
Revises: 001
Create Date: 2026-04-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("emails", sa.Column("sender_name", sa.Text(), nullable=True))
    op.add_column("emails", sa.Column("sender_type", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("emails", "sender_type")
    op.drop_column("emails", "sender_name")
