from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditLog, Course, Room, Section
from app.db.session import SessionLocal
from app.main import app
from app.services.scheduling import DEFAULT_EXPECTED_ENROLLMENT

client = TestClient(app)


def _room_matches_gender(section_gender: str, room_code: str) -> bool:
    normalized_gender = section_gender.upper()
    code = room_code.upper()
    if normalized_gender == "F":
        return "F" in code or "B" in code
    if normalized_gender == "M":
        return "M" in code or "B" in code
    return True


def _max_compatible_room_capacity(section_gender: str) -> int:
    with SessionLocal() as db:
        rooms = db.scalars(select(Room)).all()
    capacities = [
        room.capacity
        for room in rooms
        if _room_matches_gender(section_gender, room.room_code)
    ]
    return max(capacities, default=0)


def _max_room_capacity() -> int:
    with SessionLocal() as db:
        rooms = db.scalars(select(Room)).all()
    return max((room.capacity for room in rooms), default=0)


def _login_admin() -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123!"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _ensure_course(*, code: str, name: str, credit_hours: int = 3) -> Course:
    with SessionLocal() as db:
        course = db.scalar(select(Course).where(Course.code == code))
        if course is None:
            course = Course(code=code, name=name, credit_hours=credit_hours)
            db.add(course)
            db.flush()
        else:
            course.name = name
            course.credit_hours = credit_hours
        db.commit()
        db.refresh(course)
        return course


def _ensure_room(*, room_code: str, building_code: str, capacity: int) -> Room:
    with SessionLocal() as db:
        room = db.scalar(select(Room).where(Room.room_code == room_code))
        if room is None:
            room = Room(room_code=room_code, building_code=building_code, capacity=capacity)
            db.add(room)
            db.flush()
        else:
            room.building_code = building_code
            room.capacity = capacity
        db.commit()
        db.refresh(room)
        return room


def _ensure_section(
    *,
    course_id: int,
    section_code: str,
    section_type: str,
    instructor: str,
    capacity: int,
    gender_allowed: str = "BOTH",
    days: str | None = None,
    expected_enrollment: int | None = None,
) -> Section:
    with SessionLocal() as db:
        section = db.scalar(select(Section).where(Section.section_code == section_code))
        if section is None:
            section = Section(
                course_id=course_id,
                section_code=section_code,
                section_type=section_type,
                instructor=instructor,
                capacity=capacity,
                expected_enrollment=expected_enrollment,
                gender_allowed=gender_allowed,
                days=days,
            )
            db.add(section)
            db.flush()
        else:
            section.course_id = course_id
            section.section_type = section_type
            section.instructor = instructor
            section.capacity = capacity
            section.expected_enrollment = expected_enrollment
            section.gender_allowed = gender_allowed
            section.days = days
        db.commit()
        db.refresh(section)
        return section


def _room_capacity(room_id: int) -> int:
    with SessionLocal() as db:
        room = db.get(Room, room_id)
        assert room is not None
        return room.capacity


def _ensure_phase4_seed_data() -> None:
    _ensure_room(room_code="P4-A-020", building_code="P4A", capacity=20)
    _ensure_room(room_code="P4-A-030", building_code="P4A", capacity=30)
    _ensure_room(room_code="P4-B-045", building_code="P4B", capacity=45)

    course_1 = _ensure_course(code="P4C101", name="Phase4 Lecture Only")
    course_2 = _ensure_course(code="P4C102", name="Phase4 Lecture and Lab")
    course_3 = _ensure_course(code="P4C103", name="Phase4 Lecture and Tutorial")
    course_4 = _ensure_course(code="P4C104", name="Phase4 Lecture Lab Tutorial")

    _ensure_section(
        course_id=course_1.id,
        section_code="P4C101-L1",
        section_type="LECTURE",
        instructor="Phase4 Instructor A",
        capacity=18,
    )

    _ensure_section(
        course_id=course_2.id,
        section_code="P4C102-L1",
        section_type="LECTURE",
        instructor="Phase4 Instructor B",
        capacity=26,
    )
    _ensure_section(
        course_id=course_2.id,
        section_code="P4C102-B1",
        section_type="LAB",
        instructor="Phase4 Instructor B",
        capacity=26,
    )

    _ensure_section(
        course_id=course_3.id,
        section_code="P4C103-L1",
        section_type="LECTURE",
        instructor="Phase4 Instructor C",
        capacity=24,
    )
    _ensure_section(
        course_id=course_3.id,
        section_code="P4C103-T1",
        section_type="TUTORIAL",
        instructor="Phase4 Instructor C",
        capacity=24,
    )

    _ensure_section(
        course_id=course_4.id,
        section_code="P4C104-L1",
        section_type="LECTURE",
        instructor="Phase4 Instructor D",
        capacity=20,
    )
    _ensure_section(
        course_id=course_4.id,
        section_code="P4C104-B1",
        section_type="LAB",
        instructor="Phase4 Instructor D",
        capacity=20,
    )
    _ensure_section(
        course_id=course_4.id,
        section_code="P4C104-T1",
        section_type="TUTORIAL",
        instructor="Phase4 Instructor D",
        capacity=20,
    )


def _generate_schedule(token: str) -> dict:
    response = client.post(
        "/api/v1/admin/schedule/generate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    return response.json()


def _find_unscheduled(payload: dict, section_code: str) -> dict:
    return next(
        item for item in payload["unscheduled_sections"] if item["section_code"] == section_code
    )


def test_schedule_generation_has_no_room_conflicts() -> None:
    _ensure_phase4_seed_data()
    token = _login_admin()
    summary = _generate_schedule(token)
    assert summary["scheduled_sections"] > 0

    response = client.get(
        "/api/v1/admin/schedule/conflicts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["room_conflicts"] == []


def test_schedule_generation_has_no_instructor_conflicts() -> None:
    _ensure_phase4_seed_data()
    token = _login_admin()
    _generate_schedule(token)

    response = client.get(
        "/api/v1/admin/schedule/conflicts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["instructor_conflicts"] == []


def test_friday_schedule_has_no_lectures() -> None:
    _ensure_phase4_seed_data()
    token = _login_admin()
    _generate_schedule(token)

    response = client.get(
        "/api/v1/admin/schedule",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    friday_meetings = response.json()["FRI"]

    assert all(meeting["section_type"] != "LECTURE" for meeting in friday_meetings)


def test_generate_reports_unscheduled_count_matches_conflicts_found() -> None:
    _ensure_phase4_seed_data()

    course = _ensure_course(code="P4C900", name="Phase4 Unscheduled Capacity Stress")
    _ensure_section(
        course_id=course.id,
        section_code="P4C900-L1",
        section_type="LECTURE",
        instructor="Phase4 Instructor Overflow",
        capacity=9999,
    )

    token = _login_admin()
    response = client.post(
        "/api/v1/admin/schedule/generate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["conflicts_found"] == len(payload["unscheduled_sections"])

    unscheduled_response = client.get(
        "/api/v1/admin/schedule/unscheduled",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert unscheduled_response.status_code == 200
    assert unscheduled_response.json() == payload["unscheduled_sections"]


def test_female_only_section_not_scheduled_in_m_only_room() -> None:
    _ensure_phase4_seed_data()
    section_capacity = _max_compatible_room_capacity("F") + 75
    _ensure_room(
        room_code=f"P4M-ONLY-{section_capacity + 100}",
        building_code="P4M",
        capacity=section_capacity + 100,
    )

    course = _ensure_course(code="P4C910", name="Phase4 Female Room Restriction")
    section = _ensure_section(
        course_id=course.id,
        section_code="P4C910-L1",
        section_type="LECTURE",
        instructor="Phase4 Female Instructor",
        capacity=section_capacity,
        gender_allowed="F",
        expected_enrollment=section_capacity,
    )

    token = _login_admin()
    response = client.post(
        "/api/v1/admin/schedule/generate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    unscheduled = response.json()["unscheduled_sections"]

    target = next(item for item in unscheduled if item["section_id"] == section.id)
    assert target["reason"] == "NO_COMPATIBLE_GENDER_ROOM"


def test_male_only_section_not_scheduled_in_f_only_room() -> None:
    _ensure_phase4_seed_data()
    section_capacity = _max_compatible_room_capacity("M") + 75
    _ensure_room(
        room_code=f"P4F-ONLY-{section_capacity + 100}",
        building_code="P4F",
        capacity=section_capacity + 100,
    )

    course = _ensure_course(code="P4C920", name="Phase4 Male Room Restriction")
    section = _ensure_section(
        course_id=course.id,
        section_code="P4C920-L1",
        section_type="LECTURE",
        instructor="Phase4 Male Instructor",
        capacity=section_capacity,
        gender_allowed="M",
        expected_enrollment=section_capacity,
    )

    token = _login_admin()
    response = client.post(
        "/api/v1/admin/schedule/generate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    unscheduled = response.json()["unscheduled_sections"]

    target = next(item for item in unscheduled if item["section_id"] == section.id)
    assert target["reason"] == "NO_COMPATIBLE_GENDER_ROOM"


def test_friday_allows_parallel_labs_in_different_rooms() -> None:
    _ensure_phase4_seed_data()
    _ensure_room(room_code="P4B-LAB-8001", building_code="P4B", capacity=8000)
    _ensure_room(room_code="P4B-LAB-8002", building_code="P4B", capacity=8000)

    course_a = _ensure_course(code="P4C930", name="Phase4 Parallel Lab A")
    course_b = _ensure_course(code="P4C931", name="Phase4 Parallel Lab B")

    _ensure_section(
        course_id=course_a.id,
        section_code="P4C930-B1",
        section_type="LAB",
        instructor="Phase4 Friday Lab Instructor A",
        capacity=7500,
    )
    _ensure_section(
        course_id=course_b.id,
        section_code="P4C931-B1",
        section_type="LAB",
        instructor="Phase4 Friday Lab Instructor B",
        capacity=7500,
    )

    token = _login_admin()
    response = client.post(
        "/api/v1/admin/schedule/generate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    schedule_response = client.get(
        "/api/v1/admin/schedule",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert schedule_response.status_code == 200
    friday_meetings = schedule_response.json()["FRI"]

    target = [
        meeting
        for meeting in friday_meetings
        if meeting["section_code"] in {"P4C930-B1", "P4C931-B1"}
    ]

    assert len(target) == 2
    assert target[0]["start_time"] == target[1]["start_time"] == "08:00"
    assert target[0]["end_time"] == target[1]["end_time"] == "10:00"
    assert target[0]["room_code"] != target[1]["room_code"]
    assert target[0]["instructor"] != target[1]["instructor"]


def test_large_capacity_with_expected_enrollment_uses_smaller_room() -> None:
    _ensure_phase4_seed_data()
    _ensure_room(room_code="P4B-EXP-050", building_code="P4B", capacity=50)
    section_capacity = _max_room_capacity() + 100

    course = _ensure_course(code="P4C940", name="Phase4 Expected Enrollment Room Fit")
    section = _ensure_section(
        course_id=course.id,
        section_code="P4C940-L1",
        section_type="LECTURE",
        instructor="Phase4 Expected Instructor",
        capacity=section_capacity,
        expected_enrollment=35,
    )

    token = _login_admin()
    _generate_schedule(token)
    schedule_response = client.get(
        "/api/v1/admin/schedule",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert schedule_response.status_code == 200

    meetings = [
        meeting
        for day in schedule_response.json().values()
        for meeting in day
        if meeting["section_code"] == section.section_code
    ]
    assert meetings
    assert all(_room_capacity(meeting["room_id"]) >= 35 for meeting in meetings)
    assert all(_room_capacity(meeting["room_id"]) < section.capacity for meeting in meetings)


def test_null_expected_enrollment_uses_default_expected_value() -> None:
    _ensure_phase4_seed_data()
    section_capacity = _max_room_capacity() + 100

    course = _ensure_course(code="P4C941", name="Phase4 Default Expected Enrollment")
    section = _ensure_section(
        course_id=course.id,
        section_code="P4C941-L1",
        section_type="LECTURE",
        instructor="Phase4 Default Expected Instructor",
        capacity=section_capacity,
        expected_enrollment=None,
    )

    token = _login_admin()
    _generate_schedule(token)
    schedule_response = client.get(
        "/api/v1/admin/schedule",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert schedule_response.status_code == 200

    meetings = [
        meeting
        for day in schedule_response.json().values()
        for meeting in day
        if meeting["section_code"] == section.section_code
    ]
    assert meetings
    assert all(
        _room_capacity(meeting["room_id"]) >= DEFAULT_EXPECTED_ENROLLMENT for meeting in meetings
    )
    assert all(_room_capacity(meeting["room_id"]) < section.capacity for meeting in meetings)


def test_admin_can_update_and_read_expected_enrollment() -> None:
    _ensure_phase4_seed_data()
    course = _ensure_course(code="P4C942", name="Phase4 Admin Expected Enrollment")
    section = _ensure_section(
        course_id=course.id,
        section_code="P4C942-L1",
        section_type="LECTURE",
        instructor="Phase4 Admin Expected Instructor",
        capacity=120,
        expected_enrollment=None,
    )

    token = _login_admin()
    update_response = client.post(
        f"/api/v1/admin/sections/{section.id}/expected-enrollment",
        json={"expected_enrollment": 42},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["expected_enrollment"] == 42

    get_response = client.get(
        f"/api/v1/admin/sections/{section.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["expected_enrollment"] == 42

    with SessionLocal() as db:
        latest_log = db.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "SECTION_EXPECTED_ENROLLMENT_UPDATED",
                AuditLog.entity_type == "section",
                AuditLog.entity_id == str(section.id),
            )
            .order_by(AuditLog.id.desc())
        )
        assert latest_log is not None
        assert latest_log.after_data == {"expected_enrollment": 42}


def test_suggestions_include_change_instructor_for_semester_limit() -> None:
    _ensure_phase4_seed_data()
    _ensure_room(room_code="P4B-SUG-060", building_code="P4B", capacity=60)

    alt_course = _ensure_course(code="P4SUGA0", name="Phase4 Suggestion Alternate")
    _ensure_section(
        course_id=alt_course.id,
        section_code="P4SUGA0-X1",
        section_type="SEMINAR",
        instructor="Phase4 Suggestion Free",
        capacity=20,
        expected_enrollment=20,
    )

    for idx in range(1, 6):
        course = _ensure_course(
            code=f"P4SUG{idx:02d}",
            name=f"Phase4 Instructor Limit Course {idx}",
        )
        _ensure_section(
            course_id=course.id,
            section_code=f"P4SUG{idx:02d}-L1",
            section_type="LECTURE",
            instructor="Phase4 Limit Instructor",
            capacity=20,
            expected_enrollment=20,
        )

    token = _login_admin()
    generation_payload = _generate_schedule(token)
    target = _find_unscheduled(generation_payload, "P4SUG05-L1")
    assert target["reason"] == "INSTRUCTOR_SEMESTER_LIMIT"

    suggestions_response = client.get(
        "/api/v1/admin/schedule/suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert suggestions_response.status_code == 200
    suggestions = suggestions_response.json()
    section_suggestion = next(
        item for item in suggestions if item["section_id"] == target["section_id"]
    )

    change_instructor = [
        suggestion
        for suggestion in section_suggestion["suggestions"]
        if suggestion["type"] == "CHANGE_INSTRUCTOR"
    ]
    assert change_instructor
    assert change_instructor[0]["payload"]["new_instructor"] != "Phase4 Limit Instructor"


def test_suggestions_change_room_for_male_no_compatible_gender_room() -> None:
    _ensure_phase4_seed_data()
    required = _max_compatible_room_capacity("M") + 120

    course = _ensure_course(code="P4SUGM1", name="Phase4 Male Gender Room Suggestion")
    section = _ensure_section(
        course_id=course.id,
        section_code="P4SUGM1-L1",
        section_type="LECTURE",
        instructor="Phase4 Male Suggestion Instructor",
        capacity=required + 100,
        expected_enrollment=required,
        gender_allowed="M",
    )

    token = _login_admin()
    generation_payload = _generate_schedule(token)
    target = _find_unscheduled(generation_payload, section.section_code)
    assert target["reason"] == "NO_COMPATIBLE_GENDER_ROOM"

    update_response = client.post(
        f"/api/v1/admin/sections/{section.id}/expected-enrollment",
        json={"expected_enrollment": 35},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_response.status_code == 200

    suggestions_response = client.get(
        "/api/v1/admin/schedule/suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert suggestions_response.status_code == 200
    suggestions = suggestions_response.json()
    section_suggestion = next(item for item in suggestions if item["section_id"] == section.id)

    change_room = next(
        suggestion
        for suggestion in section_suggestion["suggestions"]
        if suggestion["type"] == "CHANGE_ROOM"
    )
    candidate_rooms = change_room["payload"]["candidate_rooms"]
    assert candidate_rooms
    assert all(
        "M" in candidate["room_code"].upper() or "B" in candidate["room_code"].upper()
        for candidate in candidate_rooms
    )
