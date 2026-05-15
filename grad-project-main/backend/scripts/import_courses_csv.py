"""
One-shot script to import courses from a CRN-style CSV into the database.
Usage: python3 scripts/import_courses_csv.py <path-to-csv>
"""

import csv
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from app.db.session import SessionLocal, engine
from app.db.base import Base
from app.db.models.course import Course
from app.db.models.section import Section


def infer_gender(section_code: str) -> str:
    val = section_code.strip().upper()
    if val.endswith("M"):
        return "M"
    if val.endswith("F"):
        return "F"
    return "BOTH"


def default_capacity(section_type: str) -> int:
    st = section_type.strip().upper()
    if st == "LECTURE":
        return 40
    if st == "TUTORIAL":
        return 40
    return 25


def main(csv_path: str) -> None:
    Base.metadata.create_all(bind=engine)

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with SessionLocal() as db:
        courses_added = 0
        sections_added = 0

        for row in rows:
            course_code = row["Course Code"].strip()
            course_name = row["Course Name"].strip()
            section_code = row["Section"].strip()
            section_type = row["Section Type"].strip()
            doctor_name = row["Doctor Name"].strip()
            credits_raw = row["Credits"].strip()

            try:
                credit_hours = int(float(credits_raw))
            except (ValueError, TypeError):
                credit_hours = 3
            if credit_hours <= 0:
                credit_hours = 3

            instructor = doctor_name if doctor_name.upper() != "TBA" else None
            gender = infer_gender(section_code)
            capacity = default_capacity(section_type)

            course = db.scalar(select(Course).where(Course.code == course_code))
            if course is None:
                course = Course(
                    code=course_code,
                    name=course_name,
                    credit_hours=credit_hours,
                )
                db.add(course)
                db.flush()
                courses_added += 1

            section = Section(
                course_id=course.id,
                section_code=section_code,
                section_type=section_type.upper(),
                instructor=instructor,
                capacity=capacity,
                gender_allowed=gender,
                days=None,
                start_time=None,
                end_time=None,
            )
            db.add(section)
            sections_added += 1

        db.commit()
        print(f"Done. Courses added: {courses_added}, Sections added: {sections_added}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 import_courses_csv.py <path-to-csv>")
        sys.exit(1)
    main(sys.argv[1])
