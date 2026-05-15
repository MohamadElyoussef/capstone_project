from app.db.session import SessionLocal
from app.db.models import Course
from app.db.models.course_eligibility import CourseEligibility
from sqlalchemy import text


SHARED = {
    # Data Analytics year 1
    "DAT100": [("Data Analytics", 1), ("Information Systems", 1)],
    "INT100": [("Data Analytics", 1), ("Information Systems", 1), ("Information Technology", 1)],
    "INT101": [("Data Analytics", 1), ("Information Systems", 1), ("Information Technology", 1)],

    # باقي اللي كتبتيهم (ضيفيهم بعدين)
    # "INS307": [("Information Technology", 3), ("Information Systems", 3)],
    # ...
}

def infer_year(code: str) -> int:
    digits = "".join([c for c in code if c.isdigit()])
    if not digits:
        return 1
    n = int(digits)
    if 100 <= n < 200:
        return 1
    if 200 <= n < 300:
        return 2
    if 300 <= n < 400:
        return 3
    return 4


def infer_major_by_prefix(code: str) -> str | None:
    code = code.strip().upper().replace(" ", "")
    if code.startswith("DAT"):
        return "Data Analytics"
    if code.startswith("INS"):
        return "Information Systems"
    if code.startswith("IT"):
        return "Information Technology"
    # INT غالبا مواد IT إلا اللي انتي حطيتيهم بالـ SHARED
    if code.startswith("INT"):
        return "Information Technology"
    return None


def main():
    db = SessionLocal()

    # امسحي الجدول كله واعبّي من جديد
    db.execute(text("DELETE FROM course_eligibility"))
    db.flush()

    courses = db.query(Course).all()
    added = 0

    for c in courses:
        code = (c.code or "").strip()
        code_key = code.upper().replace(" ", "")

        # 1) shared mapping
        if code_key in SHARED:
            for major, min_year in SHARED[code_key]:
                db.add(CourseEligibility(course_id=c.id, major=major, min_year_level=min_year))
                added += 1
            continue

        # 2) default based on prefix (لكل كورس تخصصه فقط)
        major = infer_major_by_prefix(code_key)
        if major is None:
            # كورس غير معروف، لا تضيفي له rules (وبالتالي strict رح يمنعه)
            continue

        min_year = infer_year(code_key)
        db.add(CourseEligibility(course_id=c.id, major=major, min_year_level=min_year))
        added += 1

    db.commit()
    db.close()
    print("Seeded course_eligibility rows:", added)


if __name__ == "__main__":
    main()