from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Course, User
from app.db.session import get_db

router = APIRouter()


class CourseResponse(BaseModel):
    id: int
    code: str
    name: str
    credit_hours: int


@router.get("", response_model=list[CourseResponse])
def list_courses(
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[CourseResponse]:
    courses = db.scalars(select(Course).order_by(Course.code)).all()
    return [
        CourseResponse(
            id=course.id,
            code=course.code,
            name=course.name,
            credit_hours=course.credit_hours,
        )
        for course in courses
    ]
