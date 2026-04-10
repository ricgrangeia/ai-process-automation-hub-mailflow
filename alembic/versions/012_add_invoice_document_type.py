"""Add document_type column to invoices

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "invoices",
        sa.Column("document_type", sa.String(10), nullable=True),
    )


def downgrade():
    op.drop_column("invoices", "document_type")
