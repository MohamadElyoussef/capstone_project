from pathlib import Path

import pandas as pd
from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import Course, Section


BASE_DIR = Path(__file__).resolve().parents[1]
XLSX_PATH = BASE_DIR / "CRN_View_File_Updated(FINAL).xlsx"


def norm(x) -> str:
    return str(x or "").replace(" ", "").strip().upper()


def main() -> None:
    if not XLSX_PATH.exists():
        raise FileNotFoundError(f"Excel file not found: {XLSX_PATH}")

    df = pd.read_excel(XLSX_PATH, sheet_name="CRN_View")

    db = SessionLocal()
    try:
        course_by_code = {
            norm(c.code): c for c in db.scalars(select(Course)).all()
        }

        updated = 0
        not_found = []

        for _, row in df.iterrows():
            course_code = norm(row.get("Course Code"))
            section_type = norm(row.get("Section Type"))
            crn = norm(row.get("CRN"))
            real_section_code = str(row.get("Section") or "").strip()

            if not course_code or not section_type or not crn or not real_section_code:
                continue

            course = course_by_code.get(course_code)
            if course is None:
                not_found.append((course_code, section_type, crn, real_section_code, "course not found"))
                continue

            section = db.scalar(
                select(Section).where(
                    Section.course_id == course.id,
                    Section.section_type == section_type,
                    Section.section_code == crn,
                )
            )

            if section is None:
                not_found.append((course_code, section_type, crn, real_section_code, "section not found"))
                continue

            if section.section_code != real_section_code:
                section.section_code = real_section_code
                updated += 1

        db.commit()

        print(f"Updated section codes: {updated}")
        print(f"Not found rows: {len(not_found)}")
        if not_found[:20]:
            print("Examples:")
            for item in not_found[:20]:
                print(item)

    finally:
        db.close()


if __name__ == "__main__":
    main()