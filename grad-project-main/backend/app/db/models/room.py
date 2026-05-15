from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    building_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    room_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    room_type: Mapped[str | None] = mapped_column(String(64), nullable=True)