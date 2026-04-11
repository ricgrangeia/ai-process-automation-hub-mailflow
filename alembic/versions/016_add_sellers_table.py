"""Add sellers table

Revision ID: 016
Revises: 015
"""
from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sellers",
        sa.Column("id",         sa.Integer(),     primary_key=True),
        sa.Column("nif",        sa.String(20),    nullable=False, unique=True),
        sa.Column("name",       sa.String(200),   nullable=True),
        sa.Column("activity",   sa.String(200),   nullable=True),
        sa.Column("cae",        sa.String(10),    nullable=True),
        sa.Column("address",    sa.String(300),   nullable=True),
        sa.Column("situation",  sa.String(50),    nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sellers_nif", "sellers", ["nif"])


def downgrade():
    op.drop_index("ix_sellers_nif", table_name="sellers")
    op.drop_table("sellers")
