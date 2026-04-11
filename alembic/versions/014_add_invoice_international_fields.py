"""Add international invoice fields to invoices table

Revision ID: 014
Revises: 013
"""
from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("invoices", sa.Column("invoice_origin",   sa.String(20),   nullable=True))
    op.add_column("invoices", sa.Column("seller_name",      sa.Text,         nullable=True))
    op.add_column("invoices", sa.Column("seller_country",   sa.String(4),    nullable=True))
    op.add_column("invoices", sa.Column("currency",         sa.String(8),    nullable=True))
    op.add_column("invoices", sa.Column("vat_rate",         sa.Numeric(5,4), nullable=True))
    op.add_column("invoices", sa.Column("receipt_number",   sa.String(64),   nullable=True))
    op.add_column("invoices", sa.Column("card_last4",       sa.String(4),    nullable=True))


def downgrade():
    for col in ["invoice_origin", "seller_name", "seller_country",
                "currency", "vat_rate", "receipt_number", "card_last4"]:
        op.drop_column("invoices", col)
