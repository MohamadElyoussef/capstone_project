from datetime import time

from sqlalchemy import CheckConstraint, ForeignKey, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScheduledMeeting(Base):
    __tablename__ = "scheduled_meetings"
    __table_args__ = (
        CheckConstraint(
            "day IN ('SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT')",
            name="ck_scheduled_meetings_day",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id"),
        index=True,
        nullable=False,
    )
    day: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id"),
        index=True,
        nullable=True,
    )