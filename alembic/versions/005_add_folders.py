"""Add folders table with default classification folders.

Folders drive the LLM prompt, Telegram buttons, and IMAP target folders.
They replace the hardcoded FOLDERS lists throughout the codebase.

Default folders seeded: Invoices, Work, Personal, Marketing, Spam, Other

Revision ID: 005
Revises: 004
Create Date: 2026-04-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_FOLDERS = ["Invoices", "Work", "Personal", "Marketing", "Spam", "Other"]


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # Seed default folders
    op.bulk_insert(
        sa.table(
            "folders",
            sa.column("name", sa.String),
            sa.column("is_active", sa.Boolean),
        ),
        [{"name": name, "is_active": True} for name in DEFAULT_FOLDERS],
    )


def downgrade() -> None:
    op.drop_table("folders")
