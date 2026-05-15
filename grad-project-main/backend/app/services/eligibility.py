from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import Course, Section, User


def _normalize_gender(value: str | None) -> str:
    if not value:
        return "BOTH"
    v = value.strip().upper()
    return v if v in {"M", "F", "BOTH"} else "BOTH"


def _section_allows_student(section_gender: str | None, student_gender: str | None) -> bool:
    sg = _normalize_gender(section_gender)
    ug = _normalize_gender(student_gender)

    if sg == "BOTH":
        return True
    if ug == "BOTH":
        return True
    return sg == ug


def build_sections_query_for_user(db: Session, user_id: int):
    """
    يرجّع Query للسكشنات المتاحة لهذا المستخدم حسب:
    - gender_allowed في Section
    - major + year_level في User مقارنة مع Course إذا كانت الأعمدة موجودة
    """
    user = db.get(User, user_id)
    if not user:
        return select(Section).where(False)

    # الأساس: فلترة الجندر على مستوى السكشن
    # (BOTH أو نفس جندر الطالب)
    student_gender = _normalize_gender(getattr(user, "gender", None))

    gender_clause = or_(
        Section.gender_allowed.is_(None),
        Section.gender_allowed == "BOTH",
        Section.gender_allowed == student_gender,
    )

    q = (
        select(Section)
        .join(Course, Course.id == Section.course_id)
        .where(gender_clause)
    )

    # فلترة major + year_level إذا Course عنده نفس الأعمدة
    user_major = getattr(user, "major", None)
    user_year = getattr(user, "year_level", None)

    # ملاحظة: إذا قاعدة البيانات عندك اسم العمود مختلف, عدلي هنا فقط
    course_major_col = getattr(Course, "major", None)
    course_year_col = getattr(Course, "year_level", None)

    if course_major_col is not None and user_major:
        q = q.where(or_(course_major_col.is_(None), course_major_col == user_major))

    if course_year_col is not None and user_year:
        q = q.where(or_(course_year_col.is_(None), course_year_col == user_year))

    return q


def assert_section_allowed_for_user(db: Session, user_id: int, section_id: int) -> tuple[bool, str]:
    user = db.get(User, user_id)
    section = db.get(Section, section_id)
    if not user or not section:
        return False, "USER_OR_SECTION_NOT_FOUND"

    # gender rule
    if not _section_allows_student(getattr(section, "gender_allowed", None), getattr(user, "gender", None)):
        return False, "SECTION_GENDER_NOT_ALLOWED"

    # major/year rule (only if Course has these columns)
    course = db.get(Course, section.course_id)
    if not course:
        return False, "COURSE_NOT_FOUND"

    user_major = getattr(user, "major", None)
    user_year = getattr(user, "year_level", None)

    course_major = getattr(course, "major", None) if hasattr(course, "major") else None
    course_year = getattr(course, "year_level", None) if hasattr(course, "year_level") else None

    if course_major is not None and user_major and course_major not in (None, user_major):
        return False, "COURSE_MAJOR_NOT_ALLOWED"

    if course_year is not None and user_year and course_year not in (None, user_year):
        return False, "COURSE_YEAR_NOT_ALLOWED"

    return True, "OK"