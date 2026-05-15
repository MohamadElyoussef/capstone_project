from collections import defaultdict
from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import Course, Section, Enrollment


def norm(x: str | None) -> str:
    return (x or "").replace(" ", "").strip().upper()


def main() -> None:
    db = SessionLocal()
    try:
        sections = db.scalars(select(Section)).all()

        grouped = defaultdict(list)
        for s in sections:
            key = (
                s.course_id,
                norm(s.section_type),
                norm(s.section_code),
            )
            grouped[key].append(s)

        deleted_sections = 0
        moved_enrollments = 0

        for key, items in grouped.items():
            if len(items) <= 1:
                continue

            # keep the first one, delete the rest
            keep = sorted(items, key=lambda x: x.id)[0]
            duplicates = sorted(items, key=lambda x: x.id)[1:]

            print(
                f"Duplicate found: course_id={key[0]}, type={key[1]}, code={key[2]}, "
                f"keep={keep.id}, delete={[x.id for x in duplicates]}"
            )

            for dup in duplicates:
                # move enrollments from duplicate section to kept section
                enrollments = db.scalars(
                    select(Enrollment).where(Enrollment.section_id == dup.id)
                ).all()

                for e in enrollments:
                    exists = db.scalar(
                        select(Enrollment).where(
                            Enrollment.user_id == e.user_id,
                            Enrollment.section_id == keep.id,
                        )
                    )
                    if exists is None:
                        e.section_id = keep.id
                        moved_enrollments += 1
                    else:
                        db.delete(e)

                db.delete(dup)
                deleted_sections += 1

        db.commit()
        print(f"Done. deleted_sections={deleted_sections}, moved_enrollments={moved_enrollments}")

    finally:
        db.close()


if __name__ == "__main__":
    main()