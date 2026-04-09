"""Add companies table

Revision ID: 010
Revises: 009
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id",         sa.Integer(),     primary_key=True),
        sa.Column("name",       sa.String(120),   nullable=False),
        sa.Column("nif",        sa.String(20),    nullable=False),
        sa.Column("active",     sa.Boolean(),     nullable=False, server_default="true"),
        sa.Column("notes",      sa.String(255),   nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_companies_nif", "companies", ["nif"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_companies_nif", "companies")
    op.drop_table("companies")
