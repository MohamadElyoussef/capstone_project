from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import Course, Section


def norm(x: str | None) -> str:
    return (x or "").replace(" ", "").strip().upper()


def main() -> None:
    db = SessionLocal()
    try:
        course = db.scalar(
            select(Course).where(Course.code == "INT436")
        )

        if course is None:
            print("INT436 course not found")
            return

        lectures = db.scalars(
            select(Section).where(
                Section.course_id == course.id,
                Section.section_type == "LECTURE",
            )
        ).all()

        lectures = sorted(lectures, key=lambda s: norm(s.section_code))

        print("Before:", [(s.id, s.section_code, s.section_type) for s in lectures])

        # خلي أول سكشنين فقط
        to_keep = lectures[:2]
        to_delete = lectures[2:]

        for sec in to_delete:
            db.delete(sec)

        db.commit()

        print("Kept:", [(s.id, s.section_code) for s in to_keep])
        print("Deleted:", [(s.id, s.section_code) for s in to_delete])

    finally:
        db.close()


if __name__ == "__main__":
    main()