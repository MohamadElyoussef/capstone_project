from datetime import datetime, time
from io import BytesIO
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.audit import log_audit
from app.db.models.completed_course import CompletedCourse
from app.db.models.course import Course
from app.db.models.course_eligibility import CourseEligibility
from app.db.models.course_prerequisite import CoursePrerequisite
from app.db.models.enrollment import Enrollment
from app.db.models.room import Room
from app.db.models.scheduled_meeting import ScheduledMeeting
from app.db.models.section import Section
from app.db.models.study_plan_mapping import StudyPlanMapping
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter()

ROOMS_REQUIRED_COLUMNS = {"room_code", "capacity"}
ROOMS_ALT_COLUMNS = {"buildingcode", "roomnumber", "roomcapacity", "roomtype"}

COURSES_REQUIRED_COLUMNS = {
    "course_code",
    "course_name",
    "credit_hours",
    "section_code",
    "section_type",
    "instructor",
    "capacity",
    "gender_allowed",
    "days",
    "start_time",
    "end_time",
}

COURSES_ALT_COLUMNS = {
    "course code",
    "course name",
    "credits",
    "section type",
    "section",
    "doctor name",
}


class AdminImportResponse(BaseModel):
    rooms_imported: int
    courses_imported: int
    sections_imported: int
    lecture_rooms_count: int = 0
    lab_rooms_count: int = 0


def _ensure_pandas() -> Any:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="pandas dependency is not installed on the server.",
        ) from exc
    return pd


def _normalize_dataframe_columns(df: Any) -> None:
    df.columns = [str(column).strip().lower() for column in df.columns]


def _required_columns_or_400(df: Any, required: set[str], file_label: str) -> None:
    missing = sorted(required - set(df.columns))
    if missing:
        missing_text = ", ".join(missing)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{file_label} is missing required columns: {missing_text}",
        )


def _filename_or_400(file: UploadFile, suffix: str, field_name: str) -> str:
    filename = file.filename or ""
    if not filename.lower().endswith(suffix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be a {suffix.upper().replace('.', '')} file.",
        )
    return filename


def _to_int(value: Any, *, field: str, row_number: int, pd: Any) -> int:
    if pd.isna(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} at row {row_number}: value is required.",
        )
    try:
        parsed = int(float(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} at row {row_number}: expected integer.",
        ) from exc
    if parsed <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} at row {row_number}: must be greater than 0.",
        )
    return parsed


def _to_text(
    value: Any,
    *,
    field: str,
    row_number: int,
    pd: Any,
    required: bool = True,
) -> str | None:
    if pd.isna(value):
        if required:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {field} at row {row_number}: value is required.",
            )
        return None

    text = str(value).strip()
    if required and not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} at row {row_number}: value is required.",
        )
    return text if text else None


def _to_time(value: Any, *, field: str, row_number: int, pd: Any) -> time | None:
    if pd.isna(value):
        return None

    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()

    text = str(value).strip()
    if not text:
        return None

    for candidate_format in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, candidate_format).time()
        except ValueError:
            continue

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid {field} at row {row_number}: expected HH:MM or HH:MM:SS.",
    )


def _building_code_from_room_code(room_code: str) -> str:
    prefix = room_code.split("-", 1)[0].strip()
    return prefix or "GEN"


def _normalize_room_number(value: str) -> str:
    room_number = str(value).strip().upper()
    if room_number.endswith(".0"):
        room_number = room_number[:-2]
    return room_number


def _normalize_instructor_for_filter(value: str | None) -> str:
    text = str(value or "").strip().upper()
    text = text.replace(".", "")
    text = " ".join(text.split())
    return text


def _should_skip_instructor(value: str | None) -> bool:
    excluded_instructors = {
        "TUTOR A",
        "LAB INSTRUCTOR",
        "DR SMITH",
    }
    return _normalize_instructor_for_filter(value) in excluded_instructors


def _map_rooms_dataframe(df: Any, pd: Any) -> Any:
    columns = set(df.columns)

    if ROOMS_REQUIRED_COLUMNS.issubset(columns):
        return df

    if ROOMS_ALT_COLUMNS.issubset(columns):
        mapped = pd.DataFrame()

        building = df["buildingcode"].astype(str).str.strip().str.upper()
        room_number = df["roomnumber"].apply(_normalize_room_number)
        room_type = df["roomtype"].astype(str).str.strip().str.lower()

        mapped["room_code"] = building + "-" + room_number
        mapped["capacity"] = pd.to_numeric(df["roomcapacity"], errors="coerce")

        is_lab = room_type.str.contains("lab", na=False)
        is_lecture_or_tutorial = (
            room_type.str.contains("lecture", na=False)
            | room_type.str.contains("tutorial", na=False)
        )

        valid_mask = is_lab | is_lecture_or_tutorial
        mapped["room_type"] = "LECTURE"
        mapped.loc[is_lab, "room_type"] = "LAB"

        mapped = mapped[valid_mask].copy()
        return mapped

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "rooms_file is missing required columns. "
            "Expected room_code, capacity "
            "or BuildingCode, RoomNumber, RoomCapacity, RoomType. "
            f"Found columns: {sorted(columns)}"
        ),
    )


def _map_courses_dataframe(df: Any, pd: Any) -> Any:
    columns = set(df.columns)

    if COURSES_REQUIRED_COLUMNS.issubset(columns):
        return df

    if COURSES_ALT_COLUMNS.issubset(columns):
        mapped = pd.DataFrame()

        mapped["course_code"] = df["course code"].astype(str).str.strip()
        mapped["course_name"] = df["course name"].astype(str).str.strip()

        mapped["credit_hours"] = pd.to_numeric(df["credits"], errors="coerce")
        mapped["credit_hours"] = mapped["credit_hours"].fillna(3)
        mapped.loc[mapped["credit_hours"] <= 0, "credit_hours"] = 3

        mapped["section_code"] = df["section"].astype(str).str.strip()
        mapped["section_type"] = df["section type"].astype(str).str.strip()
        mapped["instructor"] = df["doctor name"].astype(str).str.strip()

        expected_students_col = None
        for candidate in ["expected students", "expected stud", "expected_students"]:
            if candidate in df.columns:
                expected_students_col = candidate
                break

        if expected_students_col is not None:
            mapped["capacity"] = pd.to_numeric(
                df[expected_students_col], errors="coerce"
            ).fillna(40)
            mapped.loc[mapped["capacity"] <= 0, "capacity"] = 40
        else:
            mapped["capacity"] = 40

        def infer_gender(section_value: str) -> str:
            value = str(section_value).strip().upper()
            if value.endswith("M"):
                return "M"
            if value.endswith("F"):
                return "F"
            return "BOTH"

        mapped["gender_allowed"] = mapped["section_code"].apply(infer_gender)
        mapped["days"] = None
        mapped["start_time"] = None
        mapped["end_time"] = None

        return mapped

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "courses_file is missing required columns. "
            f"Found columns: {sorted(columns)}"
        ),
    )


def _load_rooms_dataframe(file: UploadFile, pd: Any) -> Any:
    _filename_or_400(file, ".csv", "rooms_file")
    try:
        raw = file.file.read()
        df = pd.read_csv(BytesIO(raw))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to parse rooms_file CSV.",
        ) from exc

    _normalize_dataframe_columns(df)
    df = _map_rooms_dataframe(df, pd)
    _required_columns_or_400(df, ROOMS_REQUIRED_COLUMNS, "rooms_file")
    return df


def _load_courses_dataframe(file: UploadFile, pd: Any) -> Any:
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="courses_file must be a CSV or XLSX file.",
        )

    raw = file.file.read()
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(BytesIO(raw))
        else:
            df = pd.read_excel(BytesIO(raw))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to parse courses_file.",
        ) from exc

    _normalize_dataframe_columns(df)
    df = _map_courses_dataframe(df, pd)
    _required_columns_or_400(df, COURSES_REQUIRED_COLUMNS, "courses_file")
    return df


def _clear_old_semester_data(db: Session) -> None:
    db.execute(delete(Enrollment))
    db.execute(delete(ScheduledMeeting))
    db.execute(delete(Section))
    db.execute(delete(CoursePrerequisite))
    db.execute(delete(CourseEligibility))
    db.execute(delete(CompletedCourse))
    db.execute(delete(StudyPlanMapping))
    db.execute(delete(Course))
    db.execute(delete(Room))
    db.flush()


@router.post("/import", response_model=AdminImportResponse)
def import_university_data(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    rooms_file: Annotated[UploadFile | None, File()] = None,
    courses_file: Annotated[UploadFile | None, File()] = None,
) -> AdminImportResponse:
    if courses_file is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="courses_file is required.",
        )

    pd = _ensure_pandas()
    rooms_df = _load_rooms_dataframe(rooms_file, pd) if rooms_file is not None else None
    courses_df = _load_courses_dataframe(courses_file, pd)

    try:
        if rooms_df is not None:
            _clear_old_semester_data(db)
        else:
            # Courses-only import: clear only course/section data, keep rooms
            db.execute(delete(Enrollment))
            db.execute(delete(ScheduledMeeting))
            db.execute(delete(Section))
            db.execute(delete(CoursePrerequisite))
            db.execute(delete(CourseEligibility))
            db.execute(delete(CompletedCourse))
            db.execute(delete(StudyPlanMapping))
            db.execute(delete(Course))
            db.flush()

        touched_rooms: set[str] = set()
        touched_courses: set[str] = set()
        touched_sections: set[tuple[str, str, str]] = set()
        lecture_rooms_count = 0
        lab_rooms_count = 0

        if rooms_df is not None:
            for index, row in rooms_df.iterrows():
                row_number = int(index) + 2
                room_code = _to_text(
                    row["room_code"],
                    field="room_code",
                    row_number=row_number,
                    pd=pd,
                )
                assert room_code is not None

                capacity = _to_int(
                    row["capacity"],
                    field="capacity",
                    row_number=row_number,
                    pd=pd,
                )

                room_type_val = _to_text(
                    row.get("room_type"),
                    field="room_type",
                    row_number=row_number,
                    pd=pd,
                    required=False,
                )

                room = Room(
                    room_code=room_code,
                    building_code=_building_code_from_room_code(room_code),
                    capacity=capacity,
                    room_type=room_type_val,
                )
                db.add(room)
                touched_rooms.add(room_code)

                normalized_room_type = (room_type_val or "").strip().upper()
                if normalized_room_type == "LAB":
                    lab_rooms_count += 1
                else:
                    lecture_rooms_count += 1

        db.flush()

        skipped_instructors_count = 0

        for index, row in courses_df.iterrows():
            row_number = int(index) + 2

            course_code = _to_text(
                row["course_code"],
                field="course_code",
                row_number=row_number,
                pd=pd,
            )
            course_name = _to_text(
                row["course_name"],
                field="course_name",
                row_number=row_number,
                pd=pd,
            )
            section_code = _to_text(
                row["section_code"],
                field="section_code",
                row_number=row_number,
                pd=pd,
            )
            section_type = _to_text(
                row["section_type"],
                field="section_type",
                row_number=row_number,
                pd=pd,
            )
            gender_allowed = _to_text(
                row["gender_allowed"],
                field="gender_allowed",
                row_number=row_number,
                pd=pd,
            )

            assert course_code is not None
            assert course_name is not None
            assert section_code is not None
            assert section_type is not None
            assert gender_allowed is not None

            credit_hours = _to_int(
                row["credit_hours"],
                field="credit_hours",
                row_number=row_number,
                pd=pd,
            )
            section_capacity = _to_int(
                row["capacity"],
                field="capacity",
                row_number=row_number,
                pd=pd,
            )

            instructor = _to_text(
                row["instructor"],
                field="instructor",
                row_number=row_number,
                pd=pd,
                required=False,
            )

            if _should_skip_instructor(instructor):
                skipped_instructors_count += 1
                continue

            days = _to_text(
                row["days"],
                field="days",
                row_number=row_number,
                pd=pd,
                required=False,
            )
            start_time = _to_time(
                row["start_time"],
                field="start_time",
                row_number=row_number,
                pd=pd,
            )
            end_time = _to_time(
                row["end_time"],
                field="end_time",
                row_number=row_number,
                pd=pd,
            )

            normalized_gender = gender_allowed.upper()
            if normalized_gender not in {"M", "F", "BOTH"}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid gender_allowed at row {row_number}: use M, F, or BOTH.",
                )

            course = db.scalar(select(Course).where(Course.code == course_code))
            if course is None:
                course = Course(
                    code=course_code,
                    name=course_name,
                    credit_hours=credit_hours,
                )
                db.add(course)
                db.flush()

            section = Section(
                course_id=course.id,
                section_code=section_code,
                section_type=section_type.upper(),
                instructor=instructor,
                capacity=section_capacity,
                gender_allowed=normalized_gender,
                days=days,
                start_time=start_time,
                end_time=end_time,
            )
            db.add(section)

            touched_courses.add(course_code)
            touched_sections.add((course_code, section_code, section_type.upper()))

        summary = AdminImportResponse(
            rooms_imported=len(touched_rooms),
            courses_imported=len(touched_courses),
            sections_imported=len(touched_sections),
            lecture_rooms_count=lecture_rooms_count,
            lab_rooms_count=lab_rooms_count,
        )

        log_audit(
            db,
            actor_user_id=current_user.id,
            action="ADMIN_IMPORT_REPLACE_SEMESTER",
            entity_type="data_import",
            entity_id="bulk_upload",
            before_data=None,
            after_data={
                **summary.model_dump(),
                "skipped_placeholder_instructors": skipped_instructors_count,
            },
        )

        db.commit()
        return summary

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(exc)}",
        ) from exc