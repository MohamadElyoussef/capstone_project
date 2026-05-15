"""phase3_registration_and_window

Revision ID: a513f31b923d
Revises:
Create Date: 2026-02-27 03:47:17.387065

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a513f31b923d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def upgrade() -> None:
    """Upgrade schema."""
    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("full_name", sa.String(length=120), nullable=True),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("year_level", sa.Integer(), nullable=True),
            sa.Column("gender", sa.String(length=8), nullable=True),
            sa.Column("major", sa.String(length=120), nullable=True),
            sa.UniqueConstraint("username", name="uq_users_username"),
        )
        op.create_index("ix_users_id", "users", ["id"], unique=False)
        op.create_index("ix_users_username", "users", ["username"], unique=True)
    else:
        with op.batch_alter_table("users") as batch:
            if not _has_column("users", "year_level"):
                batch.add_column(sa.Column("year_level", sa.Integer(), nullable=True))
            if not _has_column("users", "gender"):
                batch.add_column(sa.Column("gender", sa.String(length=8), nullable=True))
            if not _has_column("users", "major"):
                batch.add_column(sa.Column("major", sa.String(length=120), nullable=True))

    if not _has_table("courses"):
        op.create_table(
            "courses",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("credit_hours", sa.Integer(), nullable=False),
            sa.UniqueConstraint("code", name="uq_courses_code"),
        )
        op.create_index("ix_courses_id", "courses", ["id"], unique=False)
        op.create_index("ix_courses_code", "courses", ["code"], unique=True)

    if not _has_table("rooms"):
        op.create_table(
            "rooms",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("building_code", sa.String(length=32), nullable=False),
            sa.Column("room_code", sa.String(length=64), nullable=False),
            sa.Column("capacity", sa.Integer(), nullable=False),
            sa.UniqueConstraint("room_code", name="uq_rooms_room_code"),
        )
        op.create_index("ix_rooms_id", "rooms", ["id"], unique=False)
        op.create_index("ix_rooms_building_code", "rooms", ["building_code"], unique=False)
        op.create_index("ix_rooms_room_code", "rooms", ["room_code"], unique=True)

    if not _has_table("sections"):
        op.create_table(
            "sections",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
            sa.Column("section_code", sa.String(length=32), nullable=False),
            sa.Column(
                "section_type", sa.String(length=16), nullable=False, server_default="LECTURE"
            ),
            sa.Column("instructor", sa.String(length=120), nullable=True),
            sa.Column("capacity", sa.Integer(), nullable=False),
            sa.Column("gender_allowed", sa.String(length=8), nullable=False, server_default="BOTH"),
            sa.Column("days", sa.String(length=64), nullable=True),
            sa.Column("start_time", sa.Time(), nullable=True),
            sa.Column("end_time", sa.Time(), nullable=True),
        )
        op.create_index("ix_sections_id", "sections", ["id"], unique=False)
        op.create_index("ix_sections_course_id", "sections", ["course_id"], unique=False)
        op.create_index("ix_sections_section_code", "sections", ["section_code"], unique=False)
    else:
        with op.batch_alter_table("sections") as batch:
            if not _has_column("sections", "section_type"):
                batch.add_column(
                    sa.Column(
                        "section_type",
                        sa.String(length=16),
                        nullable=False,
                        server_default="LECTURE",
                    )
                )
            if not _has_column("sections", "gender_allowed"):
                batch.add_column(
                    sa.Column(
                        "gender_allowed",
                        sa.String(length=8),
                        nullable=False,
                        server_default="BOTH",
                    )
                )
            if not _has_column("sections", "days"):
                batch.add_column(sa.Column("days", sa.String(length=64), nullable=True))
            if not _has_column("sections", "start_time"):
                batch.add_column(sa.Column("start_time", sa.Time(), nullable=True))
            if not _has_column("sections", "end_time"):
                batch.add_column(sa.Column("end_time", sa.Time(), nullable=True))
        op.execute("UPDATE sections SET section_type = 'LECTURE' WHERE section_type IS NULL")
        op.execute("UPDATE sections SET gender_allowed = 'BOTH' WHERE gender_allowed IS NULL")

    if not _has_table("registration_settings"):
        op.create_table(
            "registration_settings",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column(
                "is_registration_open",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        )
        op.create_index(
            "ix_registration_settings_id", "registration_settings", ["id"], unique=False
        )
        op.create_index(
            "ix_registration_settings_updated_by_user_id",
            "registration_settings",
            ["updated_by_user_id"],
            unique=False,
        )

    if not _has_table("completed_courses"):
        op.create_table(
            "completed_courses",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
        )
        op.create_index("ix_completed_courses_id", "completed_courses", ["id"], unique=False)
        op.create_index(
            "ix_completed_courses_user_id", "completed_courses", ["user_id"], unique=False
        )
        op.create_index(
            "ix_completed_courses_course_id", "completed_courses", ["course_id"], unique=False
        )

    if not _has_table("study_plan_mappings"):
        op.create_table(
            "study_plan_mappings",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("major", sa.String(length=120), nullable=False),
            sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
            sa.UniqueConstraint("major", "course_id", name="uq_study_plan_major_course"),
        )
        op.create_index("ix_study_plan_mappings_id", "study_plan_mappings", ["id"], unique=False)
        op.create_index(
            "ix_study_plan_mappings_major", "study_plan_mappings", ["major"], unique=False
        )
        op.create_index(
            "ix_study_plan_mappings_course_id", "study_plan_mappings", ["course_id"], unique=False
        )

    if not _has_table("course_prerequisites"):
        op.create_table(
            "course_prerequisites",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
            sa.Column(
                "prerequisite_course_id",
                sa.Integer(),
                sa.ForeignKey("courses.id"),
                nullable=True,
            ),
            sa.Column("min_earned_credits", sa.Integer(), nullable=True),
        )
        op.create_index("ix_course_prerequisites_id", "course_prerequisites", ["id"], unique=False)
        op.create_index(
            "ix_course_prerequisites_course_id",
            "course_prerequisites",
            ["course_id"],
            unique=False,
        )
        op.create_index(
            "ix_course_prerequisites_prerequisite_course_id",
            "course_prerequisites",
            ["prerequisite_course_id"],
            unique=False,
        )

    if not _has_table("enrollments"):
        op.create_table(
            "enrollments",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("section_id", sa.Integer(), sa.ForeignKey("sections.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "section_id", name="uq_enrollment_user_section"),
        )
        op.create_index("ix_enrollments_id", "enrollments", ["id"], unique=False)
        op.create_index("ix_enrollments_user_id", "enrollments", ["user_id"], unique=False)
        op.create_index("ix_enrollments_section_id", "enrollments", ["section_id"], unique=False)

    if not _has_table("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("entity_type", sa.String(length=64), nullable=False),
            sa.Column("entity_id", sa.String(length=64), nullable=True),
            sa.Column("before_data", sa.JSON(), nullable=True),
            sa.Column("after_data", sa.JSON(), nullable=True),
        )
        op.create_index("ix_audit_logs_id", "audit_logs", ["id"], unique=False)
        op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
        op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"], unique=False)
        op.create_index(
            "ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"], unique=False
        )
        op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    if _has_table("audit_logs"):
        op.drop_table("audit_logs")
    if _has_table("enrollments"):
        op.drop_table("enrollments")
    if _has_table("course_prerequisites"):
        op.drop_table("course_prerequisites")
    if _has_table("study_plan_mappings"):
        op.drop_table("study_plan_mappings")
    if _has_table("completed_courses"):
        op.drop_table("completed_courses")
    if _has_table("registration_settings"):
        op.drop_table("registration_settings")

    if _has_table("sections"):
        with op.batch_alter_table("sections") as batch:
            if _has_column("sections", "end_time"):
                batch.drop_column("end_time")
            if _has_column("sections", "start_time"):
                batch.drop_column("start_time")
            if _has_column("sections", "days"):
                batch.drop_column("days")
            if _has_column("sections", "gender_allowed"):
                batch.drop_column("gender_allowed")
            if _has_column("sections", "section_type"):
                batch.drop_column("section_type")

    if _has_table("users"):
        with op.batch_alter_table("users") as batch:
            if _has_column("users", "major"):
                batch.drop_column("major")
            if _has_column("users", "gender"):
                batch.drop_column("gender")
            if _has_column("users", "year_level"):
                batch.drop_column("year_level")
