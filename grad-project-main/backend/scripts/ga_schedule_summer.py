from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import time
from typing import Any, Dict, List, Optional, Tuple

from ortools.sat.python import cp_model
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Course, Room, ScheduledMeeting, Section
from app.db.session import SessionLocal
import app.services.scheduling as _sched_module

print("LOADED FILE:", __file__)

# ---------------------------------------------------------------------------
# Summer scheduling constants
# ---------------------------------------------------------------------------

# 120 minutes = 4 × 30-minute slots
SUMMER_DURATION = 4

DAY_START = 8
SLOT_MINUTES = 30

# Summer days: Monday through Friday (Friday capped at noon for labs only)
SUMMER_DAYS = ["MON", "TUE", "WED", "THU", "FRI"]

LAST_END_SLOT = 23  # slots → 19:30 end  (slot 0 = 08:00)

# Friday constraint: no session may START at or after 12:00 on Friday.
# slot 4 = 10:00 start → ends at 12:00 (4 slots × 30 min = 120 min). ✓
FRIDAY_LAST_START_SLOT = 4  # 10:00 start, 12:00 end

# No break exclusion — classes are permitted during and across 12:30–13:30
BREAK_DAYS: set[str] = set()

# ---------------------------------------------------------------------------
# Course structure rules
#
#  Lecture Only            → 4 lectures/week  (MON–THU)
#  Lecture + Tutorial      → 4 lectures/week  (MON–THU) + 2 tutorials/week
#  Lecture + Lab           → 3 lectures/week  + 2 labs/week
#  Lecture + Lab + Tutorial→ 3 lectures/week  + 2 labs/week + 2 tutorials/week
# ---------------------------------------------------------------------------

# Lecture-only OR Lecture+Tutorial → 4 meetings, strictly MON-TUE-WED-THU
LECTURE_4X_PATTERNS: List[Tuple[str, List[str]]] = [
    ("MON,TUE,WED,THU", ["MON", "TUE", "WED", "THU"]),
]

# Lecture+Lab (with or without tutorial) → 3 meetings, strictly MON-TUE-WED only
LECTURE_3X_PATTERNS: List[Tuple[str, List[str]]] = [
    ("MON,TUE,WED", ["MON", "TUE", "WED"]),
]

# Labs and Tutorials → 2 meetings/week.
# Consecutive pairs: MON-TUE, TUE-WED, WED-THU, THU-FRI
# Skip-day pairs:    MON-WED, TUE-THU
# Friday sessions are capped to end by 12:00 (enforced in allowed_slots).
TWO_DAY_PATTERNS: List[Tuple[str, List[str]]] = [
    ("MON,TUE", ["MON", "TUE"]),
    ("TUE,WED", ["TUE", "WED"]),
    ("WED,THU", ["WED", "THU"]),
    ("THU,FRI", ["THU", "FRI"]),
    ("MON,WED", ["MON", "WED"]),
    ("TUE,THU", ["TUE", "THU"]),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def norm_code(value: str | None) -> str:
    return (value or "").replace(" ", "").strip().upper()


def split_days(days: str) -> List[str]:
    return [d.strip().upper() for d in days.split(",") if d.strip()]


def slot_to_time(slot: int) -> time:
    mins = DAY_START * 60 + slot * SLOT_MINUTES
    return time(mins // 60, mins % 60)


def is_lecture(section_type: str | None) -> bool:
    return norm_code(section_type) == "LECTURE"


def is_lab(section_type: str | None) -> bool:
    return norm_code(section_type) == "LAB"


def is_tutorial(section_type: str | None) -> bool:
    return norm_code(section_type) == "TUTORIAL"


def allowed_slots(days_list: List[str], duration: int) -> List[int]:
    """Return valid start slots for the given day pattern and duration.
    No break window is excluded — classes may run across 12:30–13:30.
    If the pattern includes Friday, slots are capped so sessions END by 12:00
    (start ≤ slot 4 = 10:00, end = 10:00 + 120 min = 12:00)."""
    has_friday = "FRI" in [d.upper() for d in days_list]
    max_start = FRIDAY_LAST_START_SLOT if has_friday else LAST_END_SLOT
    results = []
    for s in range(0, max_start + 1):
        if s + duration > LAST_END_SLOT:
            break
        results.append(s)
    return results


def room_suffix_part(room_code: str) -> str:
    code = norm_code(room_code)
    if "-" not in code:
        return code
    return code.split("-", 1)[1]


def is_lab_room(room_code: str) -> bool:
    suffix = room_suffix_part(room_code)
    return suffix.isdigit()


def is_non_lab_room(room_code: str) -> bool:
    return not is_lab_room(room_code)


def room_gender_bucket(room_code: str) -> str:
    code = norm_code(room_code)
    if code.startswith("J2M-"):
        return "M"
    if code.startswith("J2F-"):
        return "F"
    return "BOTH"


def intervals_overlap(start1: int, dur1: int, start2: int, dur2: int) -> bool:
    return not (start1 + dur1 <= start2 or start2 + dur2 <= start1)


def share_any_day(days1: List[str], days2: List[str]) -> bool:
    return bool(set(days1) & set(days2))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SectionInfo:
    id: int
    course_id: int
    course_code: str
    section_code: str
    section_type: str
    instructor: str
    gender_allowed: str
    capacity: int


@dataclass
class Candidate:
    sec_id: int
    days: str
    days_list: List[str]
    start: int
    dur: int
    room_id: int | None
    room_code: str | None


# ---------------------------------------------------------------------------
# Room selection
# ---------------------------------------------------------------------------

def _sort_rooms_by_capacity_asc(room_list: List[Room]) -> List[Room]:
    return sorted(room_list, key=lambda r: (int(r.capacity or 0), r.id))


def allowed_rooms_for_section(section: SectionInfo, rooms: List[Room]) -> List[Room]:
    gender = norm_code(section.gender_allowed)

    if is_lab(section.section_type):
        all_lab_rooms = [r for r in rooms if is_lab_room(r.room_code)]
        if gender == "M":
            preferred = [r for r in all_lab_rooms if room_gender_bucket(r.room_code) == "M"]
        elif gender == "F":
            preferred = [r for r in all_lab_rooms if room_gender_bucket(r.room_code) == "F"]
        else:
            preferred = all_lab_rooms

        ok = [r for r in preferred if int(r.capacity or 0) >= int(section.capacity or 0)]
        if ok:
            return _sort_rooms_by_capacity_asc(ok)
        if preferred:
            return _sort_rooms_by_capacity_asc(preferred)
        return _sort_rooms_by_capacity_asc(all_lab_rooms)

    all_non_lab = [r for r in rooms if is_non_lab_room(r.room_code)]
    if gender == "M":
        preferred = [r for r in all_non_lab if norm_code(r.room_code).endswith("M")]
    elif gender == "F":
        preferred = [r for r in all_non_lab if norm_code(r.room_code).endswith("F")]
    else:
        preferred = [r for r in all_non_lab if norm_code(r.room_code).endswith("S")]

    ok = [r for r in preferred if int(r.capacity or 0) >= int(section.capacity or 0)]
    if ok:
        return _sort_rooms_by_capacity_asc(ok)
    return _sort_rooms_by_capacity_asc(preferred)


# ---------------------------------------------------------------------------
# Course-level helpers
# ---------------------------------------------------------------------------

def has_lab_for_course(sections: List[Section], course_id: int) -> bool:
    return any(sec.course_id == course_id and is_lab(sec.section_type) for sec in sections)


def has_tutorial_for_course(sections: List[Section], course_id: int) -> bool:
    return any(sec.course_id == course_id and is_tutorial(sec.section_type) for sec in sections)


def lecture_patterns_for(has_lab: bool) -> List[Tuple[str, List[str]]]:
    """
    Summer course structure rules:
      - has_lab  → 3 lectures/week  (LECTURE_3X_PATTERNS)
      - no lab   → 4 lectures/week  (LECTURE_4X_PATTERNS)
                   covers both lecture-only and lecture+tutorial
    """
    return LECTURE_3X_PATTERNS if has_lab else LECTURE_4X_PATTERNS


# ---------------------------------------------------------------------------
# Main summer scheduling entry point
# ---------------------------------------------------------------------------

def run_summer_schedule(
    lecture_limit: int = 5,
    tutorial_limit: int = 4,
    lab_limit: int = 6,
    solver_time_seconds: int = 55,
    max_sections_per_instructor_per_day: int = 3,
    db: Optional[Session] = None,
) -> None:
    print("=== RUN_SUMMER_SCHEDULE 2026 ===")
    _owns_db = db is None
    if _owns_db:
        db = SessionLocal()
    try:
        courses = db.scalars(select(Course)).all()
        sections = db.scalars(select(Section)).all()
        rooms = db.scalars(select(Room)).all()

        if not sections:
            print("[SUMMER] no sections found")
            return

        if not rooms:
            print("[SUMMER] no rooms found")
            return

        course_map: Dict[int, Course] = {c.id: c for c in courses}

        section_infos: List[SectionInfo] = []
        for s in sections:
            course = course_map[s.course_id]
            section_infos.append(
                SectionInfo(
                    id=s.id,
                    course_id=s.course_id,
                    course_code=norm_code(course.code),
                    section_code=(s.section_code or "").strip(),
                    section_type=norm_code(s.section_type),
                    instructor=(s.instructor or "").strip(),
                    gender_allowed=norm_code(s.gender_allowed),
                    capacity=int(s.capacity or 0),
                )
            )

        print(f"[SUMMER] sections={len(section_infos)}, rooms={len(rooms)}")

        # ------------------------------------------------------------------
        # Build candidate (day-pattern, start-slot, room) tuples per section
        # ------------------------------------------------------------------
        candidates: Dict[int, List[Candidate]] = {}
        total_candidates = 0

        for s in section_infos:
            cand_list: List[Candidate] = []
            dur = SUMMER_DURATION  # all sessions = 4 slots = 120 min

            has_lab = has_lab_for_course(sections, s.course_id)

            if is_lecture(s.section_type):
                patterns = lecture_patterns_for(has_lab)
            elif is_lab(s.section_type) or is_tutorial(s.section_type):
                patterns = TWO_DAY_PATTERNS
            else:
                patterns = TWO_DAY_PATTERNS

            room_options = allowed_rooms_for_section(s, rooms)
            if not room_options:
                print(
                    f"[NO-ROOM] section={s.section_code} type={s.section_type} "
                    f"gender={s.gender_allowed} capacity={s.capacity}"
                )

            # Cap room options to avoid combinatorial explosion
            if is_lecture(s.section_type):
                room_options = room_options[:lecture_limit]
            elif is_tutorial(s.section_type):
                room_options = room_options[:tutorial_limit]
            elif is_lab(s.section_type):
                room_options = room_options[:lab_limit]

            for pattern_str, day_list in patterns:
                starts = allowed_slots(day_list, dur)
                for st in starts:
                    for r in room_options:
                        cand_list.append(
                            Candidate(
                                sec_id=s.id,
                                days=pattern_str,
                                days_list=day_list,
                                start=st,
                                dur=dur,
                                room_id=r.id,
                                room_code=r.room_code,
                            )
                        )

            candidates[s.id] = cand_list
            total_candidates += len(cand_list)

        print(f"[SUMMER] total candidates={total_candidates}")

        # ------------------------------------------------------------------
        # Build CP-SAT model
        # ------------------------------------------------------------------
        model = cp_model.CpModel()
        x: Dict[tuple[int, int], cp_model.IntVar] = {}

        for sid, cand_list in candidates.items():
            if not cand_list:
                print(f"[SUMMER][WARN] section {sid} has no candidates, will be unscheduled")
                continue
            for i in range(len(cand_list)):
                x[(sid, i)] = model.NewBoolVar(f"x_{sid}_{i}")

        # At most one candidate chosen per section
        for sid, cand_list in candidates.items():
            if not cand_list:
                continue
            model.AddAtMostOne(x[(sid, i)] for i in range(len(cand_list)))

        IGNORED_INSTRUCTORS = {"", "TBA", "STAFF", "N/A", "-"}

        # Build optional interval vars grouped by shared resource
        intervals_by_room_day: Dict[Tuple[int, str], list] = defaultdict(list)
        intervals_by_instr_day: Dict[Tuple[str, str], list] = defaultdict(list)
        presences_by_instr_day: Dict[Tuple[str, str], list] = defaultdict(list)

        section_by_id = {s.id: s for s in section_infos}

        for sid, cand_list in candidates.items():
            if not cand_list:
                continue
            sec = section_by_id[sid]
            instr = norm_code(sec.instructor)
            instr_active = instr not in IGNORED_INSTRUCTORS

            for i, cand in enumerate(cand_list):
                presence = x[(sid, i)]
                for day in cand.days_list:
                    iv = model.NewOptionalFixedSizeIntervalVar(
                        start=cand.start,
                        size=cand.dur,
                        is_present=presence,
                        name=f"iv_{sid}_{i}_{day}",
                    )
                    if cand.room_id is not None:
                        intervals_by_room_day[(cand.room_id, day)].append(iv)
                    if instr_active:
                        intervals_by_instr_day[(instr, day)].append(iv)
                        presences_by_instr_day[(instr, day)].append(presence)

        # No room overlap on the same day
        for ivs in intervals_by_room_day.values():
            if len(ivs) > 1:
                model.AddNoOverlap(ivs)

        # No instructor overlap on the same day
        for ivs in intervals_by_instr_day.values():
            if len(ivs) > 1:
                model.AddNoOverlap(ivs)

        # Cap daily teaching load per instructor
        if max_sections_per_instructor_per_day > 0:
            for presences in presences_by_instr_day.values():
                if len(presences) > max_sections_per_instructor_per_day:
                    model.Add(sum(presences) <= max_sections_per_instructor_per_day)

        # Gap constraint: back-to-back (gap = 0) or >= 2-hour gap (>= 4 slots).
        # With SUMMER_DURATION = 4, start differences of 5, 6, or 7 slots are forbidden.
        INVALID_START_DIFFS: set[int] = {5, 6, 7}

        gap_index: Dict[Tuple[str, str], Dict[Tuple[int, int], List[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for sid, cand_list in candidates.items():
            if not cand_list:
                continue
            sec = section_by_id[sid]
            instr = norm_code(sec.instructor)
            if instr in IGNORED_INSTRUCTORS:
                continue
            for i, cand in enumerate(cand_list):
                if (sid, i) not in x:
                    continue
                for day in cand.days_list:
                    gap_index[(instr, day)][(sid, cand.start)].append(i)

        for sec_slot_map in gap_index.values():
            unique_entries = list(sec_slot_map.keys())
            for k in range(len(unique_entries)):
                sid1, st1 = unique_entries[k]
                for l in range(k + 1, len(unique_entries)):
                    sid2, st2 = unique_entries[l]
                    if sid1 == sid2:
                        continue
                    if abs(st1 - st2) in INVALID_START_DIFFS:
                        lits1 = [x[(sid1, i)] for i in sec_slot_map[(sid1, st1)]]
                        lits2 = [x[(sid2, i)] for i in sec_slot_map[(sid2, st2)]]
                        model.Add(sum(lits1) + sum(lits2) <= 1)

        # Primary objective: maximise number of scheduled sections
        scheduled_sections_sum = sum(
            x[(sid, i)]
            for sid, cand_list in candidates.items()
            for i in range(len(cand_list))
            if cand_list
        )

        # Secondary objective: bonus for back-to-back sessions per instructor per day
        adjacency_bonus: list[cp_model.IntVar] = []
        for sec_slot_map in gap_index.values():
            unique_entries = list(sec_slot_map.keys())
            for k in range(len(unique_entries)):
                sid1, st1 = unique_entries[k]
                for l in range(k + 1, len(unique_entries)):
                    sid2, st2 = unique_entries[l]
                    if sid1 == sid2:
                        continue
                    if abs(st1 - st2) == SUMMER_DURATION:
                        lits1 = [x[(sid1, i)] for i in sec_slot_map[(sid1, st1)] if (sid1, i) in x]
                        lits2 = [x[(sid2, i)] for i in sec_slot_map[(sid2, st2)] if (sid2, i) in x]
                        if not lits1 or not lits2:
                            continue
                        adj_var = model.NewBoolVar(f"adj_{sid1}_{sid2}_{st1}_{st2}")
                        model.Add(sum(lits1) >= adj_var)
                        model.Add(sum(lits2) >= adj_var)
                        adjacency_bonus.append(adj_var)

        if adjacency_bonus:
            model.Maximize(scheduled_sections_sum * 100 + sum(adjacency_bonus))
        else:
            model.Maximize(scheduled_sections_sum)

        validation_error = model.Validate()
        print("[SUMMER] model validation:", validation_error if validation_error else "OK")

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(solver_time_seconds)
        solver.parameters.num_search_workers = 0
        solver.parameters.log_search_progress = False

        print(f"[SUMMER] solving (max {solver_time_seconds}s)...")
        status = solver.Solve(model)

        print("[SUMMER] solver status code =", status)
        print("[SUMMER] solver status name =", solver.StatusName(status))

        if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            print("[SUMMER] ❌ model did not solve")
            return

        print("[SUMMER] ✅ solved")

        # ------------------------------------------------------------------
        # Write results to the database
        # ------------------------------------------------------------------
        db.execute(delete(ScheduledMeeting))
        db.flush()

        raw_sections_by_id: Dict[int, Section] = {s.id: s for s in sections}

        for raw in sections:
            raw.days = None
            raw.start_time = None
            raw.end_time = None

        scheduled = 0
        unscheduled_list: List[Dict[str, Any]] = []

        for s in section_infos:
            cand_list = candidates.get(s.id, [])
            chosen: Candidate | None = None

            for i, cand in enumerate(cand_list):
                if (s.id, i) in x and solver.Value(x[(s.id, i)]) == 1:
                    chosen = cand
                    break

            if chosen is None:
                unscheduled_list.append(
                    {
                        "section_id": s.id,
                        "section_code": s.section_code,
                        "section_type": s.section_type,
                        "course_code": s.course_code,
                        "course_name": course_map[s.course_id].name or "",
                        "instructor": s.instructor or "",
                        "gender_allowed": s.gender_allowed or "",
                        "reason": "Not scheduled by summer algorithm",
                    }
                )
                continue

            scheduled += 1
            raw = raw_sections_by_id[s.id]
            raw.days = chosen.days
            raw.start_time = slot_to_time(chosen.start)
            raw.end_time = slot_to_time(chosen.start + chosen.dur)

            for day in chosen.days_list:
                db.add(
                    ScheduledMeeting(
                        section_id=raw.id,
                        day=day,
                        start_time=raw.start_time,
                        end_time=raw.end_time,
                        room_id=chosen.room_id,
                    )
                )

        db.commit()

        print(f"[SUMMER] scheduled: {scheduled}")
        print(f"[SUMMER] unscheduled: {len(section_infos) - scheduled}")

        if unscheduled_list:
            print("[SUMMER][UNSCHEDULED SECTIONS]")
            for item in unscheduled_list:
                print(f"  {item['section_id']} | {item['course_code']} | {item['section_code']} | {item['section_type']}")

        _sched_module._LAST_UNSCHEDULED_SECTIONS = unscheduled_list
        _sched_module._HAS_GENERATED_SCHEDULE = True

    finally:
        if _owns_db:
            db.close()  # type: ignore[union-attr]


if __name__ == "__main__":
    run_summer_schedule()
