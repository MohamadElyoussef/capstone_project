"""phase4_expected_enrollment

Revision ID: 9e3d1c7a4b10
Revises: f2a4c2e5b9af
Create Date: 2026-02-27 07:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e3d1c7a4b10"
down_revision: str | Sequence[str] | None = "f2a4c2e5b9af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def upgrade() -> None:
    """Upgrade schema."""
    if not _has_table("sections"):
        return

    if _has_column("sections", "expected_enrollment"):
        return

    with op.batch_alter_table("sections") as batch:
        batch.add_column(sa.Column("expected_enrollment", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    if not _has_table("sections"):
        return

    if not _has_column("sections", "expected_enrollment"):
        return

    with op.batch_alter_table("sections") as batch:
        batch.drop_column("expected_enrollment")
