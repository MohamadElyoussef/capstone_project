from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import get_password_hash
from app.db.models import Course, Room, Section, User
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)


def _ensure_test_data() -> None:
    with SessionLocal() as db:
        student = db.scalar(select(User).where(User.username == "student"))
        if student is None:
            db.add(
                User(
                    username="student",
                    password_hash=get_password_hash("Student123!"),
                    full_name="Student User",
                    role="STUDENT",
                    is_active=True,
                )
            )

        course = db.scalar(select(Course).where(Course.code == "IT101"))
        if course is None:
            course = Course(code="IT101", name="Intro to IT", credit_hours=3)
            db.add(course)
            db.flush()

        room = db.scalar(select(Room).where(Room.room_code == "A-101"))
        if room is None:
            db.add(Room(building_code="A", room_code="A-101", capacity=40))

        section = db.scalar(select(Section).where(Section.section_code == "IT101-01"))
        if section is None:
            db.add(
                Section(
                    course_id=course.id,
                    section_code="IT101-01",
                    instructor="Dr. Smith",
                    capacity=35,
                )
            )

        db.commit()


def _login(username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_rooms_admin_only() -> None:
    _ensure_test_data()
    token = _login("student", "Student123!")

    response = client.get("/api/v1/rooms", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin privileges required"


def test_rooms_admin_access() -> None:
    _ensure_test_data()
    token = _login("admin", "Admin123!")

    response = client.get("/api/v1/rooms", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert any(room["room_code"] == "A-101" for room in response.json())


def test_courses_requires_auth() -> None:
    response = client.get("/api/v1/courses")

    assert response.status_code == 401


def test_courses_authenticated() -> None:
    _ensure_test_data()
    token = _login("student", "Student123!")

    response = client.get("/api/v1/courses", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert any(course["code"] == "IT101" for course in response.json())


def test_sections_with_course_name_instructor_capacity() -> None:
    _ensure_test_data()
    token = _login("student", "Student123!")

    response = client.get("/api/v1/sections", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    section = next(item for item in response.json() if item["section_code"] == "IT101-01")
    assert section["course_name"] == "Intro to IT"
    assert section["instructor"] == "Dr. Smith"
    assert section["capacity"] == 35
