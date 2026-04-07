"""Baseline: captures existing schema as of v1.4.0.

All tables were created manually or via SQLAlchemy create_all before Alembic
was introduced. This migration is intentionally a no-op so that existing
deployments can stamp to this revision without any DDL being applied.

Revision ID: 001
Revises:
Create Date: 2026-04-05
"""
from typing import Sequence, Union

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: schema already exists in production.
    pass


def downgrade() -> None:
    # No-op: we do not tear down the baseline schema.
    pass
