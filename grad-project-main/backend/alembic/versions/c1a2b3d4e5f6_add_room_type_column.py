"""add_room_type_column

Revision ID: c1a2b3d4e5f6
Revises: f2a4c2e5b9af
Create Date: 2026-06-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1a2b3d4e5f6"
down_revision: str | Sequence[str] | None = "9e3d1c7a4b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def upgrade() -> None:
    if not _has_column("rooms", "room_type"):
        with op.batch_alter_table("rooms") as batch:
            batch.add_column(sa.Column("room_type", sa.String(length=64), nullable=True))


def downgrade() -> None:
    if _has_column("rooms", "room_type"):
        with op.batch_alter_table("rooms") as batch:
            batch.drop_column("room_type")
