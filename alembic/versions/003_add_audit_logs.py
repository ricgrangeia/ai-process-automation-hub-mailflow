"""Add audit_logs table.

Stores every auditable action in the system:
  - Email classifications and corrections
  - Learned rule creation / updates
  - Account management (add, toggle, password change)
  - Telegram admin commands (/recover, /restart, /learn)
  - Natural language queries
  - Database migration runs

Revision ID: 003
Revises: 002
Create Date: 2026-04-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # Who
        sa.Column("actor_type", sa.String(32), nullable=False),   # system | telegram | dashboard
        sa.Column("actor_name", sa.Text(), nullable=False),        # "@user", "admin", "alembic"
        # What
        sa.Column("action", sa.String(64), nullable=False),        # "email.classified", etc.
        sa.Column("entity_type", sa.String(32), nullable=True),    # email | rule | account | system | query
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),            # JSON blob
        # Tenant
        sa.Column("tenant_id", sa.Integer(), nullable=True, index=True),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_actor_name", "audit_logs", ["actor_name"])


def downgrade() -> None:
    op.drop_table("audit_logs")
