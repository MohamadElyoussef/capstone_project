from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StudyPlanMapping(Base):
    __tablename__ = "study_plan_mappings"
    __table_args__ = (UniqueConstraint("major", "course_id", name="uq_study_plan_major_course"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    major: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
