"""Add Multibanco payment fields to invoices table.

Revision ID: 007
Revises: 006
Create Date: 2026-04-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("mb_entidade",   sa.String(20),    nullable=True))
    op.add_column("invoices", sa.Column("mb_referencia", sa.String(40),    nullable=True))
    op.add_column("invoices", sa.Column("mb_valor",      sa.Numeric(12, 2), nullable=True))
    op.add_column("invoices", sa.Column("mb_data_limite", sa.String(20),   nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "mb_data_limite")
    op.drop_column("invoices", "mb_valor")
    op.drop_column("invoices", "mb_referencia")
    op.drop_column("invoices", "mb_entidade")
