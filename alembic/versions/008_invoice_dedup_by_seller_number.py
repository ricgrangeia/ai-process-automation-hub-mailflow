"""Change invoice deduplication key from email_id to (nif_seller, invoice_number).

Revision ID: 008
Revises: 007
Create Date: 2026-04-08

- Drops the unique constraint on email_id (wrong dedup key)
- Adds unique constraint on (nif_seller, invoice_number)
  PostgreSQL allows multiple NULLs in a UNIQUE constraint, so partial records
  without a decoded QR are unaffected.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove the old email_id unique constraint (auto-named by PostgreSQL)
    op.drop_constraint("invoices_email_id_key", "invoices", type_="unique")

    # Add business-key uniqueness: same seller + same invoice number = same document
    op.create_unique_constraint(
        "uq_invoices_seller_number", "invoices", ["nif_seller", "invoice_number"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_invoices_seller_number", "invoices", type_="unique")
    op.create_unique_constraint("invoices_email_id_key", "invoices", ["email_id"])
