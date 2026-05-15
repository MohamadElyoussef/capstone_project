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
        print("Before:", [(s.id, s.section_code) for s in lectures])

        if len(lectures) == 0:
            print("No INT436 lecture sections found")
            return

        if len(lectures) == 1:
            base = lectures[0]

            # create second section
            new_code = "2B"
            if norm(base.section_code) == "2B":
                new_code = "1B"

            new_section = Section(
                course_id=base.course_id,
                section_code=new_code,
                section_type="LECTURE",
                instructor=base.instructor,
                capacity=base.capacity,
                expected_enrollment=base.expected_enrollment,
                gender_allowed=base.gender_allowed,
                days=None,
                start_time=None,
                end_time=None,
            )
            db.add(new_section)
            db.commit()

            lectures = db.scalars(
                select(Section).where(
                    Section.course_id == course.id,
                    Section.section_type == "LECTURE",
                )
            ).all()
            lectures = sorted(lectures, key=lambda s: norm(s.section_code))
            print("Added second section.")
            print("After:", [(s.id, s.section_code) for s in lectures])
            return

        if len(lectures) > 2:
            keep = lectures[:2]
            delete = lectures[2:]
            for sec in delete:
                db.delete(sec)
            db.commit()
            print("Trimmed to two sections.")
            print("Kept:", [(s.id, s.section_code) for s in keep])
            return

        print("Already exactly two sections.")
        print("After:", [(s.id, s.section_code) for s in lectures])

    finally:
        db.close()


if __name__ == "__main__":
    main()