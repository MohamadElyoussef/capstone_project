from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Course, Section, User
from app.db.session import get_db

router = APIRouter()


class SectionResponse(BaseModel):
    id: int
    section_code: str
    course_name: str
    instructor: str | None
    capacity: int


@router.get("", response_model=list[SectionResponse])
def list_sections(
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[SectionResponse]:
    query = (
        select(Section.id, Section.section_code, Course.name, Section.instructor, Section.capacity)
        .join(Course, Course.id == Section.course_id)
        .order_by(Section.section_code)
    )
    rows = db.execute(query).all()
    return [
        SectionResponse(
            id=row.id,
            section_code=row.section_code,
            course_name=row.name,
            instructor=row.instructor,
            capacity=row.capacity,
        )
        for row in rows
    ]
