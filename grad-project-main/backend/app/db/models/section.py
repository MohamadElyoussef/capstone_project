from datetime import time

from sqlalchemy import ForeignKey, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True, nullable=False)
    section_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    section_type: Mapped[str] = mapped_column(String(16), nullable=False, default="LECTURE")
    instructor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_enrollment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender_allowed: Mapped[str] = mapped_column(String(8), nullable=False, default="BOTH")
    days: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
