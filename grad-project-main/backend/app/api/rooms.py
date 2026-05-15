from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.models import Room, User
from app.db.session import get_db

router = APIRouter()


class RoomResponse(BaseModel):
    id: int
    building_code: str
    room_code: str
    capacity: int


@router.get("", response_model=list[RoomResponse])
def list_rooms(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[RoomResponse]:
    rooms = db.scalars(select(Room).order_by(Room.building_code, Room.room_code)).all()
    return [
        RoomResponse(
            id=room.id,
            building_code=room.building_code,
            room_code=room.room_code,
            capacity=room.capacity,
        )
        for room in rooms
    ]
