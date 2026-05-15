"""phase4_scheduled_meetings

Revision ID: f2a4c2e5b9af
Revises: a513f31b923d
Create Date: 2026-02-27 06:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a4c2e5b9af"
down_revision: str | Sequence[str] | None = "a513f31b923d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    """Upgrade schema."""
    if _has_table("scheduled_meetings"):
        return

    op.create_table(
        "scheduled_meetings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("section_id", sa.Integer(), sa.ForeignKey("sections.id"), nullable=False),
        sa.Column("day", sa.String(length=3), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.CheckConstraint(
            "day IN ('MON', 'TUE', 'WED', 'THU', 'FRI')",
            name="ck_scheduled_meetings_day",
        ),
    )
    op.create_index("ix_scheduled_meetings_id", "scheduled_meetings", ["id"], unique=False)
    op.create_index(
        "ix_scheduled_meetings_section_id",
        "scheduled_meetings",
        ["section_id"],
        unique=False,
    )
    op.create_index("ix_scheduled_meetings_day", "scheduled_meetings", ["day"], unique=False)
    op.create_index(
        "ix_scheduled_meetings_room_id",
        "scheduled_meetings",
        ["room_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_table("scheduled_meetings"):
        op.drop_table("scheduled_meetings")
