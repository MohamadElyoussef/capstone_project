import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@dataclass(frozen=True)
class DemoStudentSeed:
    username: str
    password: str
    year_level: int
    gender: str
    major: str


DEMO_STUDENTS: list[DemoStudentSeed] = [
    DemoStudentSeed(
        username="s_da_1f",
        password="Student123!",
        year_level=1,
        gender="F",
        major="Data Analytics",
    ),
    DemoStudentSeed(
        username="s_is_2m",
        password="Student123!",
        year_level=2,
        gender="M",
        major="Information Systems",
    ),
    DemoStudentSeed(
        username="s_it_3m",
        password="Student123!",
        year_level=3,
        gender="M",
        major="Information Technology",
    ),
    DemoStudentSeed(
        username="s_it_4f",
        password="Student123!",
        year_level=4,
        gender="F",
        major="Information Technology",
    ),
]


def seed_demo_students() -> None:
    from app.core.security import get_password_hash
    from app.db.models import User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        for seed in DEMO_STUDENTS:
            existing_user = db.scalar(select(User).where(User.username == seed.username))
            if existing_user is not None:
                print(f"Skipped {seed.username}: already exists")
                continue

            user = User(
                username=seed.username,
                password_hash=get_password_hash(seed.password),
                full_name=seed.username,
                role="STUDENT",
                is_active=True,
                year_level=seed.year_level,
                gender=seed.gender,
                major=seed.major,
            )
            db.add(user)
            db.commit()
            print(
                "Created "
                f"{seed.username} "
                f"(year={seed.year_level}, gender={seed.gender}, major={seed.major})"
            )


if __name__ == "__main__":
    seed_demo_students()
