"""Add trusted column to sellers table

Revision ID: 018
Revises: 017
"""
from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sellers",
        sa.Column("trusted", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("sellers", "trusted")
