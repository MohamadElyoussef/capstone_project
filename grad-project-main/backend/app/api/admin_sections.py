from datetime import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.audit import log_audit
from app.db.models import Course, Section, User
from app.db.session import get_db

router = APIRouter()


class SectionDetailResponse(BaseModel):
    id: int
    course_id: int
    course_code: str
    course_name: str
    section_code: str
    section_type: str
    instructor: str | None
    capacity: int
    expected_enrollment: int | None
    gender_allowed: str
    days: str | None
    start_time: str | None
    end_time: str | None


class UpdateExpectedEnrollmentRequest(BaseModel):
    expected_enrollment: int = Field(ge=1, le=10000)


def _to_time_str(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _get_section_or_404(db: Session, section_id: int) -> Section:
    section = db.get(Section, section_id)
    if section is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Section not found.",
        )
    return section


def _build_section_response(db: Session, section: Section) -> SectionDetailResponse:
    course = db.get(Course, section.course_id)
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found for section.",
        )
    return SectionDetailResponse(
        id=section.id,
        course_id=course.id,
        course_code=course.code,
        course_name=course.name,
        section_code=section.section_code,
        section_type=(section.section_type or "").upper(),
        instructor=section.instructor,
        capacity=section.capacity,
        expected_enrollment=section.expected_enrollment,
        gender_allowed=(section.gender_allowed or "BOTH").upper(),
        days=section.days,
        start_time=_to_time_str(section.start_time),
        end_time=_to_time_str(section.end_time),
    )


@router.get("/{section_id}", response_model=SectionDetailResponse)
def get_admin_section(
    section_id: Annotated[int, Path(ge=1)],
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SectionDetailResponse:
    section = _get_section_or_404(db, section_id)
    return _build_section_response(db, section)


@router.post("/{section_id}/expected-enrollment", response_model=SectionDetailResponse)
def update_expected_enrollment(
    payload: UpdateExpectedEnrollmentRequest,
    section_id: Annotated[int, Path(ge=1)],
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SectionDetailResponse:
    section = _get_section_or_404(db, section_id)
    previous_value = section.expected_enrollment
    section.expected_enrollment = payload.expected_enrollment

    log_audit(
        db,
        actor_user_id=current_user.id,
        action="SECTION_EXPECTED_ENROLLMENT_UPDATED",
        entity_type="section",
        entity_id=str(section.id),
        before_data={"expected_enrollment": previous_value},
        after_data={"expected_enrollment": section.expected_enrollment},
    )
    db.commit()
    db.refresh(section)
    return _build_section_response(db, section)
