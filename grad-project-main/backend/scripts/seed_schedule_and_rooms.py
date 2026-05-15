from __future__ import annotations

from dataclasses import dataclass
from datetime import time, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import Course, Room, Section

# =========================
# Files (robust path)
# =========================
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent  # should be backend/ or BACKEND/


def _pick_existing_path(candidates: list[Path]) -> Path:
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


ROOMS_CSV_PATH = _pick_existing_path(
    [
        BASE_DIR / "J2_all_rooms_with_buildingcode.csv",
        BASE_DIR.parent / "backend" / "J2_all_rooms_with_buildingcode.csv",
        BASE_DIR.parent / "BACKEND" / "J2_all_rooms_with_buildingcode.csv",
    ]
)

CRN_XLSX_PATH = _pick_existing_path(
    [
        BASE_DIR / "CRN_View_File_Updated(FINAL).xlsx",
        BASE_DIR.parent / "backend" / "CRN_View_File_Updated(FINAL).xlsx",
        BASE_DIR.parent / "BACKEND" / "CRN_View_File_Updated(FINAL).xlsx",
    ]
)

print("RUNNING:", __file__)
print("BASE_DIR:", BASE_DIR)
print("ROOMS_CSV_PATH:", ROOMS_CSV_PATH)
print("CRN_XLSX_PATH:", CRN_XLSX_PATH)

# =========================
# Scheduling rules
# =========================
# Exceptions that must be SUN,SAT (per your rule)
SUN_SAT_COURSES = {"INT401", "INS405", "DAT403"}

LECTURE_PATTERNS = ["MON,WED", "TUE,THU"]
SUN_SAT_PATTERN = "SUN,SAT"

# Lab + Tutorial must be single day, only MON..FRI
SINGLE_DAYS = ["MON", "TUE", "WED", "THU", "FRI"]

# Time grid: 30 minutes (supports 1.5h and 2h)
DAY_START = time(8, 0)
DAY_END_REGULAR = time(19, 30)  # Mon-Thu
DAY_END_FRI = time(12, 0)       # Fri
STEP_MINUTES = 30

DAY_END_BY_DAY = {
    "MON": DAY_END_REGULAR,
    "TUE": DAY_END_REGULAR,
    "WED": DAY_END_REGULAR,
    "THU": DAY_END_REGULAR,
    "FRI": DAY_END_FRI,
}

# =========================
# Helpers
# =========================
def norm_code(x: str | None) -> str:
    return (x or "").replace(" ", "").strip().upper()


def norm_text(x: str | None) -> str:
    return (x or "").strip()


def split_days(days: str) -> list[str]:
    return [d.strip().upper() for d in (days or "").split(",") if d.strip()]


def infer_gender_from_section_code(section_code: str | None) -> str:
    sc = norm_code(section_code)
    if sc.endswith("M"):
        return "M"
    if sc.endswith("F"):
        return "F"
    return "BOTH"


def is_lab(section_type: str | None) -> bool:
    return norm_code(section_type) == "LAB"


def is_tutorial(section_type: str | None) -> bool:
    return norm_code(section_type) == "TUTORIAL"


def is_lecture(section_type: str | None) -> bool:
    return norm_code(section_type) == "LECTURE"


def _time_to_dt(t: time) -> datetime:
    return datetime(2000, 1, 1, t.hour, t.minute)


def _add_minutes(t: time, minutes: int) -> time:
    dt = _time_to_dt(t) + timedelta(minutes=minutes)
    return dt.time()


def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end


@dataclass(frozen=True)
class Meeting:
    days: str
    start: time
    end: time


@dataclass
class RoomInfo:
    id: int
    room_code: str
    capacity: int


# =========================
# Load files
# =========================
def load_rooms_csv() -> pd.DataFrame:
    if not ROOMS_CSV_PATH.exists():
        raise FileNotFoundError(f"Rooms CSV not found: {ROOMS_CSV_PATH}")
    return pd.read_csv(ROOMS_CSV_PATH)


def load_crn_xlsx() -> pd.DataFrame:
    if not CRN_XLSX_PATH.exists():
        raise FileNotFoundError(f"CRN xlsx not found: {CRN_XLSX_PATH}")
    return pd.read_excel(CRN_XLSX_PATH, sheet_name="CRN_View")


# =========================
# DB upserts/resets
# =========================
def upsert_rooms(db, rooms_df: pd.DataFrame) -> None:
    existing = {r.room_code: r for r in db.scalars(select(Room)).all()}

    for _, row in rooms_df.iterrows():
        building_code = norm_text(str(row.get("BuildingCode", "") or ""))
        room_code = norm_text(str(row.get("RoomDescription", "") or ""))
        if not room_code:
            continue

        cap_raw = row.get("Capacity", 0)
        try:
            cap = int(cap_raw) if pd.notna(cap_raw) else 0
        except Exception:
            cap = 0

        obj = existing.get(room_code)
        if obj is None:
            obj = Room(building_code=building_code, room_code=room_code, capacity=cap)
            db.add(obj)
            existing[room_code] = obj
        else:
            obj.building_code = building_code
            obj.capacity = cap

    db.flush()


def _set_section_room(section: Section, room: RoomInfo) -> None:
    if hasattr(section, "room_id"):
        setattr(section, "room_id", room.id)
        return
    if hasattr(section, "room_code"):
        setattr(section, "room_code", room.room_code)
        return


def _clear_section_room(section: Section) -> None:
    if hasattr(section, "room_id"):
        setattr(section, "room_id", None)
    if hasattr(section, "room_code"):
        setattr(section, "room_code", None)


def reset_sections(db) -> int:
    sections = db.scalars(select(Section)).all()
    for s in sections:
        s.days = None
        s.start_time = None
        s.end_time = None
        _clear_section_room(s)
        s.gender_allowed = infer_gender_from_section_code(s.section_code)
    db.flush()
    return len(sections)


# =========================
# Expected enrollment from CRN
# =========================
def build_expected_students_map(crn_df: pd.DataFrame) -> dict[tuple[str, str, str], int]:
    m: dict[tuple[str, str, str], int] = {}
    for _, row in crn_df.iterrows():
        cc = norm_code(row.get("Course Code"))
        st = norm_code(row.get("Section Type"))
        sec = norm_code(row.get("Section"))
        exp = row.get("expected students", 0)
        try:
            exp_i = int(exp) if pd.notna(exp) else 0
        except Exception:
            exp_i = 0
        if cc and st and sec:
            m[(cc, st, sec)] = exp_i
    return m


# =========================
# Rooms selection rules
# =========================
def fetch_rooms(db) -> list[RoomInfo]:
    rooms = db.scalars(select(Room)).all()
    return [RoomInfo(id=r.id, room_code=r.room_code, capacity=int(r.capacity or 0)) for r in rooms]


def room_suffix_ok(room_code: str, gender_allowed: str) -> bool:
    rc = norm_code(room_code)
    g = norm_code(gender_allowed)
    if g == "BOTH":
        return rc.endswith("S")
    if g == "M":
        return rc.endswith("M")
    if g == "F":
        return rc.endswith("F")
    return False


def is_room_lab_by_code(room_code: str) -> bool:
    rc = norm_code(room_code)
    return ("LAB" in rc) or ("COMPUTERLAB" in rc)


def pick_candidate_rooms(
    rooms: list[RoomInfo],
    *,
    section_type: str,
    gender_allowed: str,
    expected_students: int,
) -> list[RoomInfo]:
    g = norm_code(gender_allowed)
    st = norm_code(section_type)

    if st == "LAB":
        if g == "BOTH":
            special = [
                r for r in rooms if norm_code(r.room_code) in {"COMPUTERLABW1", "COMPUTERLABM1"}
            ]
            candidates = special if special else [r for r in rooms if is_room_lab_by_code(r.room_code)]
        else:
            candidates = [
                r for r in rooms if is_room_lab_by_code(r.room_code) and room_suffix_ok(r.room_code, g)
            ]
            if not candidates:
                candidates = [r for r in rooms if is_room_lab_by_code(r.room_code)]
    else:
        candidates = [
            r for r in rooms if (not is_room_lab_by_code(r.room_code)) and room_suffix_ok(r.room_code, g)
        ]

    if expected_students > 0:
        cap_ok = [r for r in candidates if r.capacity >= expected_students]
        if cap_ok:
            candidates = cap_ok

    candidates.sort(key=lambda r: (r.capacity if r.capacity > 0 else 10**9, r.room_code))
    return candidates


# =========================
# Occupancy
# =========================
class Occupancy:
    def __init__(self) -> None:
        self.room_busy: dict[tuple[int, str], list[tuple[time, time]]] = {}
        self.instr_busy: dict[tuple[str, str], list[tuple[time, time]]] = {}

    def can_place(self, room_id: int, instructor: str, days: list[str], start: time, end: time) -> bool:
        ins = norm_text(instructor)
        for d in days:
            for (s, e) in self.room_busy.get((room_id, d), []):
                if overlaps(start, end, s, e):
                    return False
            if ins:
                for (s, e) in self.instr_busy.get((ins, d), []):
                    if overlaps(start, end, s, e):
                        return False
        return True

    def place(self, room_id: int, instructor: str, days: list[str], start: time, end: time) -> None:
        ins = norm_text(instructor)
        for d in days:
            self.room_busy.setdefault((room_id, d), []).append((start, end))
            if ins:
                self.instr_busy.setdefault((ins, d), []).append((start, end))


# =========================
# Duration rules (your exact rules)
# =========================
def course_has_lab_tut(sections_for_course: list[Section]) -> tuple[bool, bool]:
    has_lab = any(is_lab(s.section_type) for s in sections_for_course)
    has_tut = any(is_tutorial(s.section_type) for s in sections_for_course)
    return has_lab, has_tut


def lecture_duration_minutes(has_lab: bool, has_tut: bool) -> int:
    if has_lab and has_tut:
        return 60
    if has_lab and not has_tut:
        return 60
    if has_tut and not has_lab:
        return 90
    return 90


def lab_duration_minutes() -> int:
    return 120


def tut_duration_minutes() -> int:
    return 120


# =========================
# Meeting generation
# =========================
def iter_start_times(duration_min: int, day_end: time) -> Iterable[time]:
    t = DAY_START
    while True:
        end = _add_minutes(t, duration_min)
        if end > day_end:
            break
        yield t
        t = _add_minutes(t, STEP_MINUTES)


def meeting_candidates_for_days(days: str, duration_min: int) -> list[Meeting]:
    dlist = split_days(days)
    if not dlist:
        return []

    # If multiple days, choose the strictest day_end among them (Friday is shortest)
    day_ends = [DAY_END_BY_DAY.get(d, DAY_END_REGULAR) for d in dlist]
    day_end = min(day_ends)

    out: list[Meeting] = []
    for start in iter_start_times(duration_min, day_end):
        end = _add_minutes(start, duration_min)
        out.append(Meeting(days=",".join(dlist), start=start, end=end))
    return out


def choose_lecture_days(course_code: str, counter: int) -> tuple[str, int]:
    cc = norm_code(course_code)
    if cc in SUN_SAT_COURSES:
        return SUN_SAT_PATTERN, counter
    chosen = LECTURE_PATTERNS[counter % len(LECTURE_PATTERNS)]
    return chosen, counter + 1


def choose_single_day_list() -> list[str]:
    return SINGLE_DAYS


# =========================
# Scheduling
# =========================
def schedule_all(db) -> None:
    rooms_df = load_rooms_csv()
    crn_df = load_crn_xlsx()

    upsert_rooms(db, rooms_df)
    reset_sections(db)

    expected_map = build_expected_students_map(crn_df)
    rooms = fetch_rooms(db)

    courses = db.scalars(select(Course)).all()
    course_by_id = {c.id: c for c in courses}

    sections = db.scalars(select(Section)).all()

    by_course: dict[int, list[Section]] = {}
    for s in sections:
        by_course.setdefault(s.course_id, []).append(s)

    def sort_key(s: Section):
        c = course_by_id.get(s.course_id)
        cc = norm_code(c.code if c else "")
        st = norm_code(s.section_type)
        pri = 2
        if st == "LECTURE":
            pri = 0
        elif st == "TUTORIAL":
            pri = 1
        return (pri, cc, norm_code(s.section_code))

    sections.sort(key=sort_key)

    occ = Occupancy()
    lecture_pattern_counter = 0

    updated = 0
    skipped = 0

    for s in sections:
        c = course_by_id.get(s.course_id)
        if c is None:
            skipped += 1
            continue

        course_code = norm_code(c.code)
        section_type = norm_code(s.section_type)
        gender = norm_code(s.gender_allowed)
        instructor = norm_text(getattr(s, "instructor", None))

        exp = expected_map.get((course_code, section_type, norm_code(s.section_code)), 0)
        if exp == 0:
            exp = expected_map.get((course_code, section_type.title(), norm_code(s.section_code)), 0)
        if exp == 0:
            exp = expected_map.get((course_code, section_type.capitalize(), norm_code(s.section_code)), 0)

        course_sections = by_course.get(s.course_id, [])
        has_lab, has_tut = course_has_lab_tut(course_sections)

        if section_type == "LECTURE":
            dur = lecture_duration_minutes(has_lab, has_tut)
            days, lecture_pattern_counter = choose_lecture_days(course_code, lecture_pattern_counter)

            # lecture forbidden on Friday, our patterns don't include FRI, so ok.
            patterns_to_try = [days]

        elif section_type == "LAB":
            dur = lab_duration_minutes()
            patterns_to_try = choose_single_day_list()

        elif section_type == "TUTORIAL":
            dur = tut_duration_minutes()
            patterns_to_try = choose_single_day_list()

        else:
            skipped += 1
            continue

        cand_rooms = pick_candidate_rooms(
            rooms,
            section_type=section_type,
            gender_allowed=gender,
            expected_students=exp,
        )

        placed = False

        for pat in patterns_to_try:
            meetings = meeting_candidates_for_days(pat, dur)
            for room in cand_rooms:
                for meet in meetings:
                    dlist = split_days(meet.days)
                    if not occ.can_place(room.id, instructor, dlist, meet.start, meet.end):
                        continue

                    s.days = meet.days
                    s.start_time = meet.start
                    s.end_time = meet.end
                    _set_section_room(s, room)

                    if getattr(s, "capacity", None) is not None:
                        s.capacity = room.capacity if room.capacity > 0 else (s.capacity or 40)

                    occ.place(room.id, instructor, dlist, meet.start, meet.end)
                    updated += 1
                    placed = True
                    break
                if placed:
                    break
            if placed:
                break

        if not placed:
            skipped += 1

    db.flush()
    print(f"Done. updated={updated} skipped={skipped}")


# =========================
# Sanity checks
# =========================
def sanity_checks(db) -> None:
    all_sections = db.scalars(select(Section)).all()
    total = len(all_sections)
    with_time = sum(1 for s in all_sections if s.start_time is not None and s.end_time is not None)
    with_days = sum(1 for s in all_sections if s.days is not None and str(s.days).strip() != "")
    print("sections total=", total, "with_time=", with_time, "with_days=", with_days)

    bad_lecture_days = []
    for s in all_sections:
        if not is_lecture(s.section_type):
            continue
        c = db.get(Course, s.course_id)
        cc = norm_code(c.code if c else "")
        d = norm_text(s.days)
        if cc in SUN_SAT_COURSES:
            if d != "SUN,SAT":
                bad_lecture_days.append((cc, s.section_code, d))
        else:
            if d not in {"MON,WED", "TUE,THU"}:
                bad_lecture_days.append((cc, s.section_code, d))
    print("bad_lecture_days_count=", len(bad_lecture_days))
    if bad_lecture_days[:20]:
        print("bad_lecture_days_examples=", bad_lecture_days[:20])

    bad_single = []
    for s in all_sections:
        if not (is_lab(s.section_type) or is_tutorial(s.section_type)):
            continue
        d = norm_text(s.days)
        if not d:
            c = db.get(Course, s.course_id)
            bad_single.append((norm_code(c.code if c else ""), s.section_type, s.section_code, d))
            continue
        dlist = split_days(d)
        if len(dlist) != 1 or dlist[0] not in SINGLE_DAYS:
            c = db.get(Course, s.course_id)
            bad_single.append((norm_code(c.code if c else ""), s.section_type, s.section_code, d))
    print("bad_lab_tutorial_single_day_count=", len(bad_single))
    if bad_single[:20]:
        print("bad_lab_tutorial_single_day_examples=", bad_single[:20])


def main() -> None:
    db = SessionLocal()
    try:
        schedule_all(db)
        db.commit()
        sanity_checks(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()