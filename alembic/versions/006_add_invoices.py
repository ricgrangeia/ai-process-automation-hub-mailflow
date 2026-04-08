"""Add invoices table for PDF QR code extracted invoice data.

Revision ID: 006
Revises: 005
Create Date: 2026-04-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email_id", sa.Integer(), sa.ForeignKey("emails.id"), nullable=False),
        sa.Column("nif_seller", sa.String(20), nullable=True),
        sa.Column("seller_name", sa.Text(), nullable=True),
        sa.Column("nif_buyer", sa.String(20), nullable=True),
        sa.Column("invoice_number", sa.String(64), nullable=True),
        sa.Column("atcud", sa.String(64), nullable=True),
        sa.Column("invoice_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("taxable_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("vat_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("raw_qr", sa.Text(), nullable=True),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_id"),
    )
    op.create_index("ix_invoices_email_id", "invoices", ["email_id"])
    op.create_index("ix_invoices_nif_seller", "invoices", ["nif_seller"])


def downgrade() -> None:
    op.drop_index("ix_invoices_nif_seller", table_name="invoices")
    op.drop_index("ix_invoices_email_id", table_name="invoices")
    op.drop_table("invoices")
