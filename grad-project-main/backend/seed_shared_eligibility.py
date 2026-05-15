from app.db.session import SessionLocal
from app.db.models import Course
from app.db.models.course_eligibility import CourseEligibility

MAJORS = {
    "IT": "Information Technology",
    "IS": "Information Systems",
    "DA": "Data Analytics",
}

SHARED = [
    ("DAT100", 1, ["IS", "DA"]),
    ("INS307", 3, ["IT", "IS"]),
    ("INS309", 3, ["IT", "IS"]),
    ("INS415", 4, ["IT", "IS"]),
    ("INT100", 1, ["IT", "IS", "DA"]),
    ("INT101", 1, ["IT", "IS", "DA"]),
    ("INT201", 2, ["IT", "IS", "DA"]),
    ("INT202", 2, ["IT", "IS", "DA"]),
    ("INT205", 2, ["IT", "IS", "DA"]),
    ("INT206", 2, ["IT", "IS", "DA"]),
    ("INT209", 2, ["IT", "DA"]),
    ("INT3013", 3, ["IT", "IS", "DA"]),   # User Interface
    ("INT302", 3, ["IT", "IS", "DA"]),
    ("INT303", 3, ["IT", "IS", "DA"]),
    ("INT306", 3, ["IT", "IS"]),
    ("INT309", 3, ["IT", "IS", "DA"]),
    ("INT323", 3, ["IT", "IS"]),
]

def main():
    db = SessionLocal()

    codes = sorted({code for code, _, _ in SHARED})
    courses = db.query(Course).filter(Course.code.in_(codes)).all()
    by_code = {c.code.upper(): c for c in courses}

    missing = [c for c in codes if c.upper() not in by_code]
    if missing:
        print("Missing Course.code in DB:", missing)
        db.close()
        return

    for code, min_year, majors in SHARED:
        course = by_code[code.upper()]
        db.query(CourseEligibility).filter(
            CourseEligibility.course_id == course.id
        ).delete()

        for m in majors:
            db.add(
                CourseEligibility(
                    course_id=course.id,
                    major=MAJORS[m],
                    min_year_level=min_year,
                )
            )

    db.commit()
    db.close()
    print("Shared eligibility seeded successfully")

if __name__ == "__main__":
    main()