"""Add managed_by column to email_accounts

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "email_accounts",
        sa.Column(
            "managed_by",
            sa.String(20),
            nullable=True,
            server_default="ai_worker",
            comment="'ai_worker' | 'invoice_worker' — which worker manages this account",
        ),
    )


def downgrade():
    op.drop_column("email_accounts", "managed_by")
