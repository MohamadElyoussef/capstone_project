import os
import re
import sys
from datetime import time

# يخلي import app يشتغل حتى لو شغلنا الملف مباشرة
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.db.session import SessionLocal
from app.db.models import Section


DAY_PATTERNS = [
    "SUN,TUE,THU",
    "MON,WED",
    "TUE,THU",
    "SUN,TUE",
    "MON,THU",
]

TIME_SLOTS = [
    (time(8, 0), time(9, 0)),
    (time(9, 0), time(10, 0)),
    (time(10, 0), time(11, 0)),
    (time(11, 0), time(12, 0)),
    (time(12, 0), time(13, 0)),
    (time(13, 0), time(14, 0)),
    (time(14, 0), time(15, 0)),
    (time(15, 0), time(16, 0)),
]


def infer_gender_allowed(section: Section) -> str:
    """
    يحاول يستنتج الجنس من section_code خصوصا للـ LAB/TUTORIAL لأن عندك أكواد مثل:
    4F, 4M, F2, M2 ... إلخ
    lectures غالبا رقمية، نخليها BOTH
    """
    code = (section.section_code or "").upper().strip()

    if section.section_type == "LECTURE":
        return "BOTH"

    # إذا الكود فيه F وما فيه M => F
    # إذا الكود فيه M وما فيه F => M
    has_f = "F" in code
    has_m = "M" in code

    if has_f and not has_m:
        return "F"
    if has_m and not has_f:
        return "M"
    return "BOTH"


def choose_schedule(section: Section) -> tuple[str, time, time]:
    """
    يولد days/start/end بشكل ثابت (deterministic) حسب IDs
    """
    seed = (section.course_id or 0) * 100000 + section.id
    day = DAY_PATTERNS[seed % len(DAY_PATTERNS)]

    # نخلي LAB/TUTORIAL تميل لأوقات متأخرة شوي عن LECTURE
    offset = 2 if section.section_type in ("LAB", "TUTORIAL") else 0
    start, end = TIME_SLOTS[(seed + offset) % len(TIME_SLOTS)]
    return day, start, end


def main() -> None:
    db = SessionLocal()
    try:
        sections = db.query(Section).all()

        updated_days_time = 0
        updated_gender = 0

        for s in sections:
            # 1) gender_allowed
            new_gender = infer_gender_allowed(s)
            if (s.gender_allowed or "").upper().strip() != new_gender:
                s.gender_allowed = new_gender
                updated_gender += 1

            # 2) days + time
            if not s.days or s.start_time is None or s.end_time is None:
                day, start, end = choose_schedule(s)
                s.days = day
                s.start_time = start
                s.end_time = end
                updated_days_time += 1

        db.commit()
        print("Done.")
        print("updated_gender =", updated_gender)
        print("updated_days_time =", updated_days_time)

    finally:
        db.close()


if __name__ == "__main__":
    main()