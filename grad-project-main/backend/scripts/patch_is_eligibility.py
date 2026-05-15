from app.db.session import SessionLocal
from app.db.models import Course
from app.db.models.course_eligibility import CourseEligibility

# المواد اللي لازم تظهر لطلاب IS (حتى لو كان الكود في DB فيه مسافات)
WANTED = {
    "INT302": 2,
    "INT303": 2,
    "INT306": 2,
    "INT307": 2,
    "INT323": 2,
    "INS308": 2,
    "INS404": 2,
    "INS405": 2,
    "INS413": 2,
    "INS415": 2,
}

TARGET_MAJOR = "Information Systems"


def norm(code: str) -> str:
    return (code or "").replace(" ", "").upper()


def main() -> None:
    db = SessionLocal()

    # ابني خريطة: normalized_code -> [Course objects]
    courses = db.query(Course).all()
    by_norm: dict[str, list[Course]] = {}
    for c in courses:
        by_norm.setdefault(norm(c.code), []).append(c)

    added = 0
    missing = []

    for wanted_code, min_year in WANTED.items():
        matches = by_norm.get(wanted_code, [])
        if not matches:
            missing.append(wanted_code)
            continue

        # ممكن يكون في أكثر من Course بنفس الكود (نادرا), بنضيف للجميع
        for course in matches:
            exists = (
                db.query(CourseEligibility)
                .filter(
                    CourseEligibility.course_id == course.id,
                    CourseEligibility.major == TARGET_MAJOR,
                )
                .first()
            )
            if exists:
                # حدث min_year_level لو كان غلط
                if int(exists.min_year_level) != int(min_year):
                    exists.min_year_level = int(min_year)
                continue

            db.add(
                CourseEligibility(
                    course_id=course.id,
                    major=TARGET_MAJOR,
                    min_year_level=int(min_year),
                )
            )
            added += 1

    db.commit()
    db.close()

    print("Added/Updated IS eligibility rules:", added)
    if missing:
        print("These course codes were NOT found in courses table:", missing)


if __name__ == "__main__":
    main()