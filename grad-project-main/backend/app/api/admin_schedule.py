import threading
import uuid
from collections import defaultdict
from datetime import datetime, time
from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.audit import log_audit
from app.db.models import AuditLog, Course, Room, ScheduledMeeting, Section, User
from app.db.session import get_db
from app.services.registration import registration_is_open, set_registration_open
from app.services.scheduling import (
    detect_schedule_conflicts,
    get_last_unscheduled_sections,
    get_unscheduled_suggestions,
    get_weekly_schedule,
)
from app.db.session import SessionLocal
from scripts.ga_schedule_sections import run_ga_schedule
from scripts.ga_schedule_summer import run_summer_schedule

router = APIRouter()

# In-memory job store for async schedule generation.
# Keys are short hex job IDs; values are dicts with status/result/detail.
_schedule_jobs: Dict[str, Any] = {}


class RegistrationStatusResponse(BaseModel):
    is_open: bool


class UnscheduledSectionResponse(BaseModel):
    section_id: int
    section_code: str
    section_type: str
    course_code: str
    course_name: str
    instructor: str
    gender_allowed: str
    reason: str


class ScheduleGenerationResponse(BaseModel):
    total_sections: int
    scheduled_sections: int
    conflicts_found: int
    unscheduled_sections: list[UnscheduledSectionResponse]


class ScheduledMeetingResponse(BaseModel):
    meeting_id: int
    section_id: int
    section_code: str
    section_type: str
    course_id: int
    course_code: str
    course_name: str
    instructor: str | None
    room_id: int | None
    room_code: str | None
    day: str
    start_time: str
    end_time: str


class ScheduleConflictResponse(BaseModel):
    day: str
    overlap_start: str
    overlap_end: str
    first_meeting_id: int
    second_meeting_id: int
    first_section_id: int
    first_section_code: str
    second_section_id: int
    second_section_code: str
    room_id: int | None = None
    room_code: str | None = None
    instructor: str | None = None


class ScheduleConflictReportResponse(BaseModel):
    room_conflicts: list[ScheduleConflictResponse]
    instructor_conflicts: list[ScheduleConflictResponse]


class ScheduleSuggestionEntry(BaseModel):
    type: str
    message: str
    payload: dict


class SectionSuggestionResponse(BaseModel):
    section_id: int
    section_code: str
    reason: str
    suggestions: list[ScheduleSuggestionEntry]


class AuditLogResponse(BaseModel):
    id: int
    timestamp: str
    actor_user_id: int | None
    actor_username: str | None
    action: str
    entity_type: str
    entity_id: str | None
    before_data: dict[str, Any] | None
    after_data: dict[str, Any] | None


class DoctorListItemResponse(BaseModel):
    instructor: str
    sections_count: int


class DoctorScheduleMeetingResponse(BaseModel):
    section_id: int
    section_code: str
    section_type: str
    course_code: str
    course_name: str
    day: str
    start_time: str
    end_time: str
    room_code: str | None


class ManualScheduleUpdateRequest(BaseModel):
    days: str
    start_time: str
    end_time: str
    room_code: str | None


class ManualScheduleUpdateResponse(BaseModel):
    message: str
    section_id: int
    days: str
    start_time: str
    end_time: str
    room_id: int | None
    room_code: str | None


def _build_generate_summary(db: Session) -> dict:
    rows = db.execute(
        select(Section, Course).join(Course, Course.id == Section.course_id)
    ).all()

    total_sections = len(rows)
    scheduled_sections = sum(
        1
        for s, _c in rows
        if s.days is not None and s.start_time is not None and s.end_time is not None
    )

    conflicts = detect_schedule_conflicts(db)
    room_conflicts = conflicts.get("room_conflicts", [])
    instructor_conflicts = conflicts.get("instructor_conflicts", [])
    conflicts_found = len(room_conflicts) + len(instructor_conflicts)

    unscheduled_sections = []
    for s, c in rows:
        if s.days is None or s.start_time is None or s.end_time is None:
            unscheduled_sections.append(
                {
                    "section_id": s.id,
                    "section_code": s.section_code or "",
                    "section_type": s.section_type or "",
                    "course_code": c.code or "",
                    "course_name": c.name or "",
                    "instructor": s.instructor or "",
                    "gender_allowed": s.gender_allowed or "",
                    "reason": "Not scheduled by GA",
                }
            )

    return {
        "total_sections": total_sections,
        "scheduled_sections": scheduled_sections,
        "conflicts_found": conflicts_found,
        "unscheduled_sections": unscheduled_sections,
    }


def _parse_time_or_400(value: str, field_name: str) -> time:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            continue

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid {field_name}. Use HH:MM or HH:MM:SS.",
    )


def _split_days_or_400(days: str) -> list[str]:
    allowed_days = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"}
    parts = [d.strip().upper() for d in days.split(",") if d.strip()]

    if not parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="days is required.",
        )

    invalid = [d for d in parts if d not in allowed_days]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid days: {', '.join(invalid)}",
        )

    return parts


def _times_overlap(
    start1: time,
    end1: time,
    start2: time,
    end2: time,
) -> bool:
    return not (end1 <= start2 or end2 <= start1)


@router.get("/registration-status", response_model=RegistrationStatusResponse)
def get_registration_status(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationStatusResponse:
    return RegistrationStatusResponse(is_open=registration_is_open(db))


@router.post("/registration/open", response_model=RegistrationStatusResponse)
def open_registration(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationStatusResponse:
    setting = set_registration_open(db, True, current_user.id)

    log_audit(
        db,
        actor_user_id=current_user.id,
        action="REGISTRATION_OPENED",
        entity_type="registration",
        entity_id=str(setting.id),
        before_data=None,
        after_data={"is_open": setting.is_registration_open},
    )
    db.commit()
    return RegistrationStatusResponse(is_open=bool(setting.is_registration_open))


@router.post("/registration/close", response_model=RegistrationStatusResponse)
def close_registration(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationStatusResponse:
    setting = set_registration_open(db, False, current_user.id)

    log_audit(
        db,
        actor_user_id=current_user.id,
        action="REGISTRATION_CLOSED",
        entity_type="registration",
        entity_id=str(setting.id),
        before_data=None,
        after_data={"is_open": setting.is_registration_open},
    )
    db.commit()
    return RegistrationStatusResponse(is_open=bool(setting.is_registration_open))


class GenerateScheduleRequest(BaseModel):
    lecture_limit: int = 5
    tutorial_limit: int = 4
    lab_limit: int = 6
    solver_time_seconds: int = 180


@router.get("/data-status")
def get_data_status(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    sections_count = db.scalar(select(func.count(Section.id))) or 0
    rooms_count = db.scalar(select(func.count(Room.id))) or 0
    return {"sections_count": sections_count, "rooms_count": rooms_count}


@router.post("/generate", response_model=ScheduleGenerationResponse)
def generate_schedule(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    body: GenerateScheduleRequest = GenerateScheduleRequest(),
) -> ScheduleGenerationResponse:
    sections_count = db.scalar(select(func.count(Section.id))) or 0
    rooms_count = db.scalar(select(func.count(Room.id))) or 0
    if sections_count == 0 or rooms_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No data imported. Please import a Rooms CSV and Courses CSV before generating a schedule.",
        )
    try:
        run_ga_schedule(
            lecture_limit=body.lecture_limit,
            tutorial_limit=body.tutorial_limit,
            lab_limit=body.lab_limit,
            solver_time_seconds=body.solver_time_seconds,
            db=db,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Schedule generation failed: {exc}",
        ) from exc

    db.expire_all()

    summary = _build_generate_summary(db)

    log_audit(
        db,
        actor_user_id=current_user.id,
        action="SCHEDULE_GENERATED_GA",
        entity_type="schedule",
        entity_id="weekly",
        before_data=None,
        after_data={
            "total_sections": summary["total_sections"],
            "scheduled_sections": summary["scheduled_sections"],
            "conflicts_found": summary["conflicts_found"],
        },
    )
    db.commit()
    return ScheduleGenerationResponse(**summary)


class RoomOptionResponse(BaseModel):
    id: int
    room_code: str
    room_type: str | None = None
    capacity: int = 0


class GenerateSummerScheduleRequest(BaseModel):
    lecture_limit: int = 5
    tutorial_limit: int = 4
    lab_limit: int = 6
    solver_time_seconds: int = 55


@router.get("/active-type")
def get_active_schedule_type(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    log = db.scalar(
        select(AuditLog)
        .where(AuditLog.action.in_(["SCHEDULE_GENERATED_GA", "SCHEDULE_GENERATED_SUMMER"]))
        .order_by(AuditLog.timestamp.desc())
        .limit(1)
    )
    if log is None or log.action == "SCHEDULE_GENERATED_GA":
        return {"schedule_type": "regular"}
    return {"schedule_type": "summer"}


@router.post("/generate-summer", response_model=ScheduleGenerationResponse)
def generate_summer_schedule(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    body: GenerateSummerScheduleRequest = GenerateSummerScheduleRequest(),
) -> ScheduleGenerationResponse:
    sections_count = db.scalar(select(func.count(Section.id))) or 0
    rooms_count = db.scalar(select(func.count(Room.id))) or 0
    if sections_count == 0 or rooms_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No data imported. Please import a Rooms CSV and Courses CSV before generating a schedule.",
        )
    try:
        run_summer_schedule(
            lecture_limit=body.lecture_limit,
            tutorial_limit=body.tutorial_limit,
            lab_limit=body.lab_limit,
            solver_time_seconds=body.solver_time_seconds,
            db=db,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Summer schedule generation failed: {exc}",
        ) from exc

    db.expire_all()

    summary = _build_generate_summary(db)

    log_audit(
        db,
        actor_user_id=current_user.id,
        action="SCHEDULE_GENERATED_SUMMER",
        entity_type="schedule",
        entity_id="weekly",
        before_data=None,
        after_data={
            "total_sections": summary["total_sections"],
            "scheduled_sections": summary["scheduled_sections"],
            "conflicts_found": summary["conflicts_found"],
        },
    )
    db.commit()
    return ScheduleGenerationResponse(**summary)


@router.post("/generate-async")
def generate_schedule_async(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    body: GenerateScheduleRequest = GenerateScheduleRequest(),
) -> dict:
    sections_count = db.scalar(select(func.count(Section.id))) or 0
    rooms_count = db.scalar(select(func.count(Room.id))) or 0
    if sections_count == 0 or rooms_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No data imported. Please import a Rooms CSV and Courses CSV before generating a schedule.",
        )

    job_id = uuid.uuid4().hex[:12]
    _schedule_jobs[job_id] = {"status": "running"}

    user_id = current_user.id
    lec, tut, lab, secs = body.lecture_limit, body.tutorial_limit, body.lab_limit, body.solver_time_seconds

    def _run() -> None:
        job_db = SessionLocal()
        try:
            run_ga_schedule(
                lecture_limit=lec, tutorial_limit=tut,
                lab_limit=lab, solver_time_seconds=secs, db=job_db,
            )
            job_db.expire_all()
            summary = _build_generate_summary(job_db)
            log_audit(
                job_db, actor_user_id=user_id, action="SCHEDULE_GENERATED_GA",
                entity_type="schedule", entity_id="weekly", before_data=None,
                after_data={"total_sections": summary["total_sections"],
                            "scheduled_sections": summary["scheduled_sections"],
                            "conflicts_found": summary["conflicts_found"]},
            )
            job_db.commit()
            _schedule_jobs[job_id] = {
                "status": "done",
                "result": ScheduleGenerationResponse(**summary).model_dump(),
            }
        except Exception as exc:
            _schedule_jobs[job_id] = {"status": "error", "detail": str(exc)}
        finally:
            job_db.close()

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@router.post("/generate-summer-async")
def generate_summer_schedule_async(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    body: GenerateSummerScheduleRequest = GenerateSummerScheduleRequest(),
) -> dict:
    sections_count = db.scalar(select(func.count(Section.id))) or 0
    rooms_count = db.scalar(select(func.count(Room.id))) or 0
    if sections_count == 0 or rooms_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No data imported. Please import a Rooms CSV and Courses CSV before generating a schedule.",
        )

    job_id = uuid.uuid4().hex[:12]
    _schedule_jobs[job_id] = {"status": "running"}

    user_id = current_user.id
    lec, tut, lab, secs = body.lecture_limit, body.tutorial_limit, body.lab_limit, body.solver_time_seconds

    def _run() -> None:
        job_db = SessionLocal()
        try:
            run_summer_schedule(
                lecture_limit=lec, tutorial_limit=tut,
                lab_limit=lab, solver_time_seconds=secs, db=job_db,
            )
            job_db.expire_all()
            summary = _build_generate_summary(job_db)
            log_audit(
                job_db, actor_user_id=user_id, action="SCHEDULE_GENERATED_SUMMER",
                entity_type="schedule", entity_id="weekly", before_data=None,
                after_data={"total_sections": summary["total_sections"],
                            "scheduled_sections": summary["scheduled_sections"],
                            "conflicts_found": summary["conflicts_found"]},
            )
            job_db.commit()
            _schedule_jobs[job_id] = {
                "status": "done",
                "result": ScheduleGenerationResponse(**summary).model_dump(),
            }
        except Exception as exc:
            _schedule_jobs[job_id] = {"status": "error", "detail": str(exc)}
        finally:
            job_db.close()

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/job-status/{job_id}")
def get_schedule_job_status(
    _: Annotated[User, Depends(require_admin)],
    job_id: str,
) -> dict:
    job = _schedule_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    return job


class ClearScheduleResponse(BaseModel):
    meetings_deleted: int
    message: str


@router.delete("/clear", response_model=ClearScheduleResponse)
def clear_schedule(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ClearScheduleResponse:
    meetings_deleted = db.scalar(select(func.count(ScheduledMeeting.id))) or 0

    db.execute(delete(ScheduledMeeting))

    db.execute(
        Section.__table__.update().values(
            days=None,
            start_time=None,
            end_time=None,
        )
    )

    log_audit(
        db,
        actor_user_id=current_user.id,
        action="SCHEDULE_CLEARED",
        entity_type="schedule",
        entity_id="all",
        before_data={"meetings_deleted": meetings_deleted},
        after_data=None,
    )
    db.commit()

    return ClearScheduleResponse(
        meetings_deleted=meetings_deleted,
        message=f"Schedule cleared. {meetings_deleted} meeting(s) removed.",
    )


@router.get("/rooms", response_model=list[RoomOptionResponse])
def get_rooms(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    section_type: str | None = None,
):
    query = select(Room).order_by(Room.room_code.asc())
    if section_type:
        normalized = section_type.strip().upper()
        query = query.where(func.upper(Room.room_type) == normalized)
    rooms = db.scalars(query).all()
    return [
        RoomOptionResponse(
            id=r.id,
            room_code=r.room_code,
            room_type=r.room_type,
            capacity=r.capacity,
        )
        for r in rooms
    ]


class RoomBookingEntry(BaseModel):
    day: str
    start_time: str
    end_time: str
    section_code: str


class RoomAvailabilityEntry(BaseModel):
    id: int
    room_code: str
    room_type: str | None = None
    bookings: list[RoomBookingEntry]
    is_free: bool


@router.get("/rooms/available", response_model=list[RoomAvailabilityEntry])
def get_available_rooms(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    section_type: str | None = None,
    days: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    exclude_section_id: int | None = None,
):
    room_query = select(Room).order_by(Room.room_code.asc())
    if section_type:
        st = section_type.strip().upper()
        expected_room_type = "LAB" if st == "LAB" else "LECTURE"
        room_query = room_query.where(func.upper(Room.room_type) == expected_room_type)
    rooms = db.scalars(room_query).all()

    parsed_start: time | None = None
    parsed_end: time | None = None
    requested_days: list[str] = []

    if days and start_time and end_time:
        try:
            parsed_start = _parse_time_or_400(start_time, "start_time")
            parsed_end = _parse_time_or_400(end_time, "end_time")
            requested_days = [d.strip().upper() for d in days.split(",") if d.strip()]
        except HTTPException:
            pass

    result: list[RoomAvailabilityEntry] = []

    for room in rooms:
        meeting_rows = db.execute(
            select(ScheduledMeeting, Section)
            .join(Section, Section.id == ScheduledMeeting.section_id)
            .where(ScheduledMeeting.room_id == room.id)
            .order_by(ScheduledMeeting.day.asc(), ScheduledMeeting.start_time.asc())
        ).all()

        bookings: list[RoomBookingEntry] = []
        is_free = True

        for meeting, section in meeting_rows:
            if exclude_section_id and section.id == exclude_section_id:
                continue

            bookings.append(
                RoomBookingEntry(
                    day=meeting.day,
                    start_time=meeting.start_time.strftime("%H:%M"),
                    end_time=meeting.end_time.strftime("%H:%M"),
                    section_code=section.section_code or "",
                )
            )

            if parsed_start and parsed_end and requested_days:
                if meeting.day in requested_days and _times_overlap(
                    parsed_start, parsed_end, meeting.start_time, meeting.end_time
                ):
                    is_free = False

        if not (parsed_start and parsed_end and requested_days):
            is_free = len(bookings) == 0

        result.append(
            RoomAvailabilityEntry(
                id=room.id,
                room_code=room.room_code,
                room_type=room.room_type,
                bookings=bookings,
                is_free=is_free,
            )
        )

    result.sort(key=lambda r: (not r.is_free, r.room_code))
    return result


@router.get("/unscheduled", response_model=list[UnscheduledSectionResponse])
def get_unscheduled_sections(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[UnscheduledSectionResponse]:
    rows = db.execute(
        select(Section, Course)
        .join(Course, Course.id == Section.course_id)
        .where(
            (Section.days.is_(None)) | (Section.start_time.is_(None)) | (Section.end_time.is_(None))
        )
    ).all()
    return [
        UnscheduledSectionResponse(
            section_id=s.id,
            section_code=s.section_code or "",
            section_type=s.section_type or "",
            course_code=c.code or "",
            course_name=c.name or "",
            instructor=s.instructor or "",
            gender_allowed=s.gender_allowed or "",
            reason="Not scheduled",
        )
        for s, c in rows
    ]


@router.get("/suggestions", response_model=list[SectionSuggestionResponse])
def get_schedule_suggestions(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[SectionSuggestionResponse]:
    return [SectionSuggestionResponse(**item) for item in get_unscheduled_suggestions(db)]


@router.get("", response_model=dict[str, list[ScheduledMeetingResponse]])
def get_schedule(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, list[ScheduledMeetingResponse]]:
    return get_weekly_schedule(db)


@router.get("/conflicts", response_model=ScheduleConflictReportResponse)
def get_schedule_conflicts(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ScheduleConflictReportResponse:
    return ScheduleConflictReportResponse(**detect_schedule_conflicts(db))


@router.get("/audit-logs", response_model=list[AuditLogResponse])
def get_audit_logs(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AuditLogResponse]:
    logs = db.scalars(
        select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(200)
    ).all()

    user_ids = {log.actor_user_id for log in logs if log.actor_user_id is not None}
    users_by_id: dict[int, str] = {}

    if user_ids:
        users = db.scalars(select(User).where(User.id.in_(user_ids))).all()
        users_by_id = {user.id: user.username for user in users}

    return [
        AuditLogResponse(
            id=log.id,
            timestamp=log.timestamp.isoformat(),
            actor_user_id=log.actor_user_id,
            actor_username=users_by_id.get(log.actor_user_id) if log.actor_user_id is not None else None,
            action=log.action,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            before_data=log.before_data,
            after_data=log.after_data,
        )
        for log in logs
    ]


class AvailableInstructorResponse(BaseModel):
    instructor: str
    max_daily_load: int


@router.get("/sections/{section_id}/available-instructors", response_model=list[AvailableInstructorResponse])
def get_available_instructors_for_section(
    section_id: int,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AvailableInstructorResponse]:
    section = db.scalar(select(Section).where(Section.id == section_id))
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found.")

    section_days: list[str] = []
    if section.days:
        section_days = [d.strip().upper() for d in section.days.split(",") if d.strip()]

    ignored = {"", "TBA", "STAFF", "N/A", "-"}

    all_instructors: list[str] = sorted(
        set(
            db.scalars(
                select(Section.instructor)
                .where(
                    Section.instructor.is_not(None),
                    Section.instructor != "",
                    ~Section.instructor.in_(list(ignored)),
                )
                .distinct()
            ).all()
        ),
        key=lambda x: x.lower(),
    )

    result: list[AvailableInstructorResponse] = []
    for instr in all_instructors:
        if not section_days:
            result.append(AvailableInstructorResponse(instructor=instr, max_daily_load=0))
            continue

        day_counts: dict[str, int] = {}
        rows = db.execute(
            select(ScheduledMeeting.day, func.count(ScheduledMeeting.id))
            .join(Section, Section.id == ScheduledMeeting.section_id)
            .where(Section.instructor == instr)
            .group_by(ScheduledMeeting.day)
        ).all()
        for row in rows:
            day_counts[row[0]] = int(row[1] or 0)

        max_load = max((day_counts.get(d, 0) for d in section_days), default=0)
        if max_load < 3:
            result.append(AvailableInstructorResponse(instructor=instr, max_daily_load=max_load))

    result.sort(key=lambda r: (r.max_daily_load, r.instructor.lower()))
    return result


@router.get("/doctors", response_model=list[DoctorListItemResponse])
def get_doctors(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DoctorListItemResponse]:
    rows = db.execute(
        select(
            Section.instructor,
            func.count(Section.id),
        )
        .where(
            Section.instructor.is_not(None),
            Section.instructor != "",
        )
        .group_by(Section.instructor)
        .order_by(func.lower(Section.instructor).asc())
    ).all()

    return [
        DoctorListItemResponse(
            instructor=row[0],
            sections_count=int(row[1] or 0),
        )
        for row in rows
    ]


@router.get("/doctor-courses", response_model=dict[str, list[str]])
def get_doctor_courses(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, list[str]]:
    """Return a mapping of instructor_name -> [course_names] for all non-TBA lecture sections."""
    rows = db.execute(
        select(Section.instructor, Course.name)
        .join(Course, Course.id == Section.course_id)
        .where(
            Section.section_type.ilike("LECTURE"),
            Section.instructor.is_not(None),
            Section.instructor != "",
            func.upper(Section.instructor) != "TBA",
        )
        .order_by(func.lower(Section.instructor).asc(), Course.name.asc())
    ).all()

    mapping: dict[str, list[str]] = defaultdict(list)
    for instructor, course_name in rows:
        if course_name not in mapping[instructor]:
            mapping[instructor].append(course_name)

    return dict(mapping)


@router.get("/doctors/{instructor_name}/schedule", response_model=dict[str, list[DoctorScheduleMeetingResponse]])
def get_doctor_schedule(
    instructor_name: str,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, list[DoctorScheduleMeetingResponse]]:
    rows = db.execute(
        select(ScheduledMeeting, Section, Course, Room)
        .join(Section, Section.id == ScheduledMeeting.section_id)
        .join(Course, Course.id == Section.course_id)
        .outerjoin(Room, Room.id == ScheduledMeeting.room_id)
        .where(Section.instructor == instructor_name)
        .order_by(ScheduledMeeting.day.asc(), ScheduledMeeting.start_time.asc())
    ).all()

    grouped: dict[str, list[DoctorScheduleMeetingResponse]] = defaultdict(list)

    for meeting, section, course, room in rows:
        grouped[meeting.day].append(
            DoctorScheduleMeetingResponse(
                section_id=section.id,
                section_code=section.section_code or "",
                section_type=section.section_type or "",
                course_code=course.code or "",
                course_name=course.name or "",
                day=meeting.day,
                start_time=meeting.start_time.isoformat(),
                end_time=meeting.end_time.isoformat(),
                room_code=room.room_code if room else None,
            )
        )

    for day in grouped:
        grouped[day].sort(key=lambda x: x.start_time)

    return dict(grouped)


_BREAK_DAYS = {"TUE", "THU"}
_BREAK_START = time(12, 30)
_BREAK_END = time(13, 30)


def _overlaps_break(day: str, start: time, end: time) -> bool:
    return day.upper() in _BREAK_DAYS and _times_overlap(start, end, _BREAK_START, _BREAK_END)


# Summer 3-day pattern: lecture+lab courses (MON/TUE/WED)
_LECTURE_DAY_PAIR_3X = [("MON,TUE,WED", ["MON", "TUE", "WED"])]
# Summer 4-day pattern: lecture-only or lecture+tutorial courses (MON/TUE/WED/THU)
_LECTURE_DAY_PAIR_4X = [("MON,TUE,WED,THU", ["MON", "TUE", "WED", "THU"])]

_LECTURE_SLOTS_90 = [
    ("08:00", "09:30"), ("09:30", "11:00"), ("11:00", "12:30"),
    ("13:30", "15:00"), ("15:00", "16:30"), ("16:30", "18:00"),
    ("18:00", "19:30"),
]
_LECTURE_SLOTS_120 = [
    ("08:00", "10:00"), ("10:00", "12:00"), ("12:00", "14:00"),
    ("14:00", "16:00"), ("16:00", "18:00"),
]
_LAB_TUTORIAL_DAYS = ["MON", "TUE", "WED", "THU", "FRI"]
_LAB_TUTORIAL_SLOTS = [
    ("08:00", "10:00"), ("10:00", "12:00"), ("12:00", "14:00"),
    ("14:00", "16:00"), ("16:00", "18:00"),
]


class AvailableSlotRoom(BaseModel):
    id: int
    room_code: str


class AvailableSlotResponse(BaseModel):
    days: str
    start_time: str
    end_time: str
    available_rooms: list[AvailableSlotRoom]


_REGULAR_LECTURE_DAY_PAIRS = [
    ("MON,WED", ["MON", "WED"]),
    ("TUE,THU", ["TUE", "THU"]),
]

_SUMMER_LAB_TUTORIAL_DAY_PAIRS = [
    ("MON,TUE", ["MON", "TUE"]),
    ("TUE,WED", ["TUE", "WED"]),
    ("WED,THU", ["WED", "THU"]),
]

_FRI_CUTOFF = time(12, 0)


@router.get("/sections/{section_id}/available-slots", response_model=list[AvailableSlotResponse])
def get_available_slots(
    section_id: int,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    schedule_type: str = "regular",
) -> list[AvailableSlotResponse]:
    section = db.scalar(select(Section).where(Section.id == section_id))
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found.")

    is_summer = schedule_type.lower() == "summer"
    section_type = (section.section_type or "").upper()
    instructor = section.instructor

    expected_room_type = "LAB" if section_type in ("LAB", "PRACTICAL") else "LECTURE"
    rooms_of_type = db.scalars(
        select(Room)
        .where(func.upper(Room.room_type) == expected_room_type)
        .order_by(Room.room_code.asc())
    ).all()

    room_meetings = db.scalars(
        select(ScheduledMeeting).where(
            ScheduledMeeting.room_id.is_not(None),
            ScheduledMeeting.section_id != section_id,
        )
    ).all()
    room_busy: dict[int, list] = defaultdict(list)
    for m in room_meetings:
        room_busy[m.room_id].append((m.day, m.start_time, m.end_time))

    instructor_busy: list = []
    if instructor:
        instr_rows = db.scalars(
            select(ScheduledMeeting)
            .join(Section, Section.id == ScheduledMeeting.section_id)
            .where(
                Section.instructor == instructor,
                ScheduledMeeting.section_id != section_id,
            )
        ).all()
        instructor_busy = [(m.day, m.start_time, m.end_time) for m in instr_rows]

    def instructor_free(days: list[str], start: time, end: time) -> bool:
        return not any(
            d in days and _times_overlap(start, end, bs, be)
            for d, bs, be in instructor_busy
        )

    def free_rooms(days: list[str], start: time, end: time) -> list[AvailableSlotRoom]:
        out = []
        for room in rooms_of_type:
            busy = room_busy.get(room.id, [])
            if not any(d in days and _times_overlap(start, end, bs, be) for d, bs, be in busy):
                out.append(AvailableSlotRoom(id=room.id, room_code=room.room_code))
        return out

    def fri_allowed(days_list: list[str], start: time) -> bool:
        return not ("FRI" in days_list and start >= _FRI_CUTOFF)

    result: list[AvailableSlotResponse] = []

    if is_summer:
        if section_type == "LECTURE":
            has_lab = (
                db.scalar(
                    select(func.count(Section.id)).where(
                        Section.course_id == section.course_id,
                        func.upper(Section.section_type) == "LAB",
                    )
                )
                or 0
            ) > 0
            lecture_day_pairs = _LECTURE_DAY_PAIR_3X if has_lab else _LECTURE_DAY_PAIR_4X
            for days_str, days_list in lecture_day_pairs:
                for start_str, end_str in _LECTURE_SLOTS_120:
                    start = datetime.strptime(start_str, "%H:%M").time()
                    end = datetime.strptime(end_str, "%H:%M").time()
                    if not fri_allowed(days_list, start):
                        continue
                    if not instructor_free(days_list, start, end):
                        continue
                    rooms = free_rooms(days_list, start, end)
                    if rooms:
                        result.append(AvailableSlotResponse(
                            days=days_str, start_time=start_str, end_time=end_str,
                            available_rooms=rooms,
                        ))
        else:
            for days_str, days_list in _SUMMER_LAB_TUTORIAL_DAY_PAIRS:
                for start_str, end_str in _LAB_TUTORIAL_SLOTS:
                    start = datetime.strptime(start_str, "%H:%M").time()
                    end = datetime.strptime(end_str, "%H:%M").time()
                    if not fri_allowed(days_list, start):
                        continue
                    if not instructor_free(days_list, start, end):
                        continue
                    rooms = free_rooms(days_list, start, end)
                    if rooms:
                        result.append(AvailableSlotResponse(
                            days=days_str, start_time=start_str, end_time=end_str,
                            available_rooms=rooms,
                        ))
    else:
        if section_type == "LECTURE":
            for days_str, days_list in _REGULAR_LECTURE_DAY_PAIRS:
                for start_str, end_str in _LECTURE_SLOTS_90:
                    start = datetime.strptime(start_str, "%H:%M").time()
                    end = datetime.strptime(end_str, "%H:%M").time()
                    if any(_overlaps_break(d, start, end) for d in days_list):
                        continue
                    if not fri_allowed(days_list, start):
                        continue
                    if not instructor_free(days_list, start, end):
                        continue
                    rooms = free_rooms(days_list, start, end)
                    if rooms:
                        result.append(AvailableSlotResponse(
                            days=days_str, start_time=start_str, end_time=end_str,
                            available_rooms=rooms,
                        ))
        else:
            for day in _LAB_TUTORIAL_DAYS:
                for start_str, end_str in _LAB_TUTORIAL_SLOTS:
                    start = datetime.strptime(start_str, "%H:%M").time()
                    end = datetime.strptime(end_str, "%H:%M").time()
                    if _overlaps_break(day, start, end):
                        continue
                    if not fri_allowed([day], start):
                        continue
                    if not instructor_free([day], start, end):
                        continue
                    rooms = free_rooms([day], start, end)
                    if rooms:
                        result.append(AvailableSlotResponse(
                            days=day, start_time=start_str, end_time=end_str,
                            available_rooms=rooms,
                        ))

    return result


class UpdateInstructorRequest(BaseModel):
    instructor_name: str


@router.patch("/sections/{section_id}/instructor")
def update_section_instructor(
    section_id: int,
    body: UpdateInstructorRequest,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    section = db.scalar(select(Section).where(Section.id == section_id))
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found.")

    old_instructor = section.instructor
    new_instructor = body.instructor_name.strip()
    section.instructor = new_instructor

    log_audit(
        db,
        actor_user_id=current_user.id,
        action="INSTRUCTOR_ASSIGNED",
        entity_type="section",
        entity_id=str(section_id),
        before_data={"instructor": old_instructor},
        after_data={"instructor": new_instructor},
    )
    db.commit()
    return {"section_id": section_id, "instructor": new_instructor}


class UpdateCourseInstructorRequest(BaseModel):
    instructor_name: str


@router.patch("/courses/{course_code}/instructor")
def update_course_instructor(
    course_code: str,
    body: UpdateCourseInstructorRequest,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    course = db.scalar(select(Course).where(Course.code == course_code))
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Course '{course_code}' not found.",
        )

    new_instructor = body.instructor_name.strip()
    sections = list(db.scalars(select(Section).where(Section.course_id == course.id)).all())

    for section in sections:
        old_instructor = section.instructor
        section.instructor = new_instructor
        log_audit(
            db,
            actor_user_id=current_user.id,
            action="INSTRUCTOR_ASSIGNED",
            entity_type="section",
            entity_id=str(section.id),
            before_data={"instructor": old_instructor},
            after_data={"instructor": new_instructor},
        )

    db.commit()
    return {
        "course_code": course_code,
        "instructor": new_instructor,
        "sections_updated": len(sections),
    }


class BulkUpdateInstructorRequest(BaseModel):
    section_ids: list[int]
    instructor_name: str


@router.patch("/sections/bulk-instructor")
def bulk_update_section_instructor(
    body: BulkUpdateInstructorRequest,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    new_instructor = body.instructor_name.strip()
    sections = list(
        db.scalars(select(Section).where(Section.id.in_(body.section_ids))).all()
    )
    for section in sections:
        old_instructor = section.instructor
        section.instructor = new_instructor
        log_audit(
            db,
            actor_user_id=current_user.id,
            action="INSTRUCTOR_ASSIGNED",
            entity_type="section",
            entity_id=str(section.id),
            before_data={"instructor": old_instructor},
            after_data={"instructor": new_instructor},
        )
    db.commit()
    return {"sections_updated": len(sections), "instructor": new_instructor}


@router.patch(
    "/sections/{section_id}/manual-update",
    response_model=ManualScheduleUpdateResponse,
)
def manual_update_section_schedule(
    section_id: int,
    payload: ManualScheduleUpdateRequest,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ManualScheduleUpdateResponse:
    section = db.scalar(select(Section).where(Section.id == section_id))

    if section is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Section not found.",
        )

    parsed_days = _split_days_or_400(payload.days)

    # تعديل ذكي للـ lectures
    if section.section_type and section.section_type.upper() == "LECTURE":
        if parsed_days == ["MON"]:
            parsed_days = ["MON", "WED"]
        elif parsed_days == ["WED"]:
            parsed_days = ["MON", "WED"]
        elif parsed_days == ["TUE"]:
            parsed_days = ["TUE", "THU"]
        elif parsed_days == ["THU"]:
            parsed_days = ["TUE", "THU"]

    parsed_start = _parse_time_or_400(payload.start_time, "start_time")
    parsed_end = _parse_time_or_400(payload.end_time, "end_time")

    if parsed_end <= parsed_start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_time must be after start_time.",
        )

    for day in parsed_days:
        if _overlaps_break(day, parsed_start, parsed_end):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot schedule between 12:30 and 13:30 on Tuesday/Thursday — reserved break.",
            )

    room = None
    if payload.room_code:
        room = db.scalar(select(Room).where(Room.room_code == payload.room_code))
        if room is None:
            raise HTTPException(status_code=404, detail="Room not found")

    old_data = {
        "days": section.days,
        "start_time": section.start_time.isoformat() if section.start_time else None,
        "end_time": section.end_time.isoformat() if section.end_time else None,
        "room_ids": [
            meeting.room_id
            for meeting in db.scalars(
                select(ScheduledMeeting).where(ScheduledMeeting.section_id == section.id)
            ).all()
        ],
    }

    existing_meetings = db.execute(
        select(ScheduledMeeting, Section, Room)
        .join(Section, Section.id == ScheduledMeeting.section_id)
        .outerjoin(Room, Room.id == ScheduledMeeting.room_id)
        .where(ScheduledMeeting.section_id != section.id)
    ).all()

    normalized_instructor = (section.instructor or "").strip().upper()
    ignored_instructors = {"", "TBA", "STAFF", "N/A", "-"}

    for day in parsed_days:
        for meeting, other_section, other_room in existing_meetings:
            if meeting.day != day:
                continue

            if not _times_overlap(parsed_start, parsed_end, meeting.start_time, meeting.end_time):
                continue

            if room is not None and meeting.room_id == room.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Room conflict on {day} with section "
                        f"{other_section.section_code or other_section.id}."
                    ),
                )

            other_instructor = (other_section.instructor or "").strip().upper()
            if (
                normalized_instructor not in ignored_instructors
                and other_instructor not in ignored_instructors
                and normalized_instructor == other_instructor
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Instructor conflict on {day} with section "
                        f"{other_section.section_code or other_section.id}."
                    ),
                )

    db.execute(delete(ScheduledMeeting).where(ScheduledMeeting.section_id == section.id))
    db.flush()

    section.days = ",".join(parsed_days)
    section.start_time = parsed_start
    section.end_time = parsed_end

    for day in parsed_days:
        db.add(
            ScheduledMeeting(
                section_id=section.id,
                day=day,
                start_time=parsed_start,
                end_time=parsed_end,
                room_id=room.id if room else None,
            )
        )

    new_data = {
        "days": section.days,
        "start_time": section.start_time.isoformat() if section.start_time else None,
        "end_time": section.end_time.isoformat() if section.end_time else None,
        "room_id": room.id if room else None,
        "room_code": room.room_code if room else None,
    }

    log_audit(
        db,
        actor_user_id=current_user.id,
        action="SCHEDULE_MANUAL_UPDATED",
        entity_type="section_schedule",
        entity_id=str(section.id),
        before_data=old_data,
        after_data=new_data,
    )

    db.commit()

    return ManualScheduleUpdateResponse(
        message="Section schedule updated successfully.",
        section_id=section.id,
        days=section.days or "",
        start_time=section.start_time.isoformat() if section.start_time else "",
        end_time=section.end_time.isoformat() if section.end_time else "",
        room_id=room.id if room else None,
        room_code=room.room_code if room else None,
    )