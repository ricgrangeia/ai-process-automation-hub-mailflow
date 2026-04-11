"""Add document_type_description column to invoices

Revision ID: 013
Revises: 012
"""
from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "invoices",
        sa.Column("document_type_description", sa.String(60), nullable=True),
    )


def downgrade():
    op.drop_column("invoices", "document_type_description")
