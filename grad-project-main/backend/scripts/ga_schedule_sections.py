from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import time
from typing import Dict, List, Tuple

from ortools.sat.python import cp_model
from sqlalchemy import delete, select

from app.db.models import Course, Room, ScheduledMeeting, Section
from app.db.session import SessionLocal

print("LOADED FILE:", __file__)

SUN_SAT_COURSES = {"INT401", "INS405", "DAT403"}

LECTURE_PATTERNS = ["MON,WED", "TUE,THU"]
SINGLE_DAYS = ["MON", "TUE", "WED", "THU", "FRI"]

DAY_START = 8
SLOT_MINUTES = 30

LAST_END_SLOT_REGULAR = 23
LAST_END_SLOT_FRI = 8


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


def last_end_slot_for_day(day: str) -> int:
    return LAST_END_SLOT_FRI if day == "FRI" else LAST_END_SLOT_REGULAR


BREAK_DAYS = {"TUE", "THU"}
BREAK_START_SLOT = 9   # 12:30
BREAK_END_SLOT = 11    # 13:30


def slot_overlaps_break(day: str, start: int, duration: int) -> bool:
    if day not in BREAK_DAYS:
        return False
    return start < BREAK_END_SLOT and start + duration > BREAK_START_SLOT


def allowed_slots_sparse(day: str, duration: int) -> List[int]:
    # step=1 → both :00 and :30 starts (e.g. 8:00, 8:30, 9:00, 9:30 ...)
    limit = last_end_slot_for_day(day)
    upper = min(limit, 16)
    return [
        s for s in range(0, upper + 1)
        if s + duration <= limit and not slot_overlaps_break(day, s, duration)
    ]


def allowed_slots_dense(day: str, duration: int) -> List[int]:
    # step=1 → both :00 and :30 starts across the full day
    limit = last_end_slot_for_day(day)
    return [
        s for s in range(0, limit + 1)
        if s + duration <= limit and not slot_overlaps_break(day, s, duration)
    ]


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


@dataclass
class Candidate:
    sec_id: int
    days: str
    days_list: List[str]
    start: int
    dur: int
    room_id: int | None
    room_code: str | None


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


def has_lab_for_course(sections: List[Section], course_id: int) -> bool:
    return any(sec.course_id == course_id and is_lab(sec.section_type) for sec in sections)


def has_tutorial_for_course(sections: List[Section], course_id: int) -> bool:
    return any(sec.course_id == course_id and is_tutorial(sec.section_type) for sec in sections)


def lecture_duration_slots(has_lab: bool, has_tut: bool) -> int:
    if has_lab:
        return 2
    if has_tut:
        return 3
    return 3


def duration_slots(section_type: str, has_lab: bool, has_tut: bool) -> int:
    if is_lecture(section_type):
        return lecture_duration_slots(has_lab, has_tut)
    if is_lab(section_type):
        return 4
    if is_tutorial(section_type):
        return 4
    return 2


def _sort_rooms_by_capacity_asc(room_list: List[Room]) -> List[Room]:
    return sorted(room_list, key=lambda r: (int(r.capacity or 0), r.id))


def allowed_rooms_for_section(section: SectionInfo, rooms: List[Room]) -> List[Room]:
    gender = norm_code(section.gender_allowed)

    if is_lab(section.section_type):
        all_lab_rooms = [r for r in rooms if is_lab_room(r.room_code)]

        if gender == "M":
            preferred = [r for r in all_lab_rooms if room_gender_bucket(r.room_code) == "M"]
            fallback = preferred
        elif gender == "F":
            preferred = [r for r in all_lab_rooms if room_gender_bucket(r.room_code) == "F"]
            fallback = preferred
        else:
            preferred = all_lab_rooms
            fallback = all_lab_rooms

        preferred_capacity = [r for r in preferred if int(r.capacity or 0) >= int(section.capacity or 0)]
        if preferred_capacity:
            return _sort_rooms_by_capacity_asc(preferred_capacity)

        fallback_capacity = [r for r in fallback if int(r.capacity or 0) >= int(section.capacity or 0)]
        if fallback_capacity:
            return _sort_rooms_by_capacity_asc(fallback_capacity)

        if preferred:
            return _sort_rooms_by_capacity_asc(preferred)

        return _sort_rooms_by_capacity_asc(fallback)

    all_non_lab_rooms = [r for r in rooms if is_non_lab_room(r.room_code)]

    if gender == "M":
        preferred = [r for r in all_non_lab_rooms if norm_code(r.room_code).endswith("M")]
    elif gender == "F":
        preferred = [r for r in all_non_lab_rooms if norm_code(r.room_code).endswith("F")]
    else:
        preferred = [r for r in all_non_lab_rooms if norm_code(r.room_code).endswith("S")]

    preferred_capacity = [r for r in preferred if int(r.capacity or 0) >= int(section.capacity or 0)]
    if preferred_capacity:
        return _sort_rooms_by_capacity_asc(preferred_capacity)

    return _sort_rooms_by_capacity_asc(preferred)


def intervals_overlap(start1: int, dur1: int, start2: int, dur2: int) -> bool:
    return not (start1 + dur1 <= start2 or start2 + dur2 <= start1)


def share_any_day(days1: List[str], days2: List[str]) -> bool:
    return bool(set(days1) & set(days2))


def run_ga_schedule(
    lecture_limit: int = 5,
    tutorial_limit: int = 4,
    lab_limit: int = 6,
    solver_time_seconds: int = 180,
    max_sections_per_instructor_per_day: int = 3,
    max_instructor_gap_slots: int = 4,
) -> None:
    print("=== RUN_GA_SCHEDULE FAST 2026-04-24 ===")
    db = SessionLocal()
    try:
        courses = db.scalars(select(Course)).all()
        sections = db.scalars(select(Section)).all()
        rooms = db.scalars(select(Room)).all()

        if not sections:
            print("[CP] no sections found")
            return

        if not rooms:
            print("[CP] no rooms found")
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

        print(f"[CP] sections={len(section_infos)}, rooms={len(rooms)}")

        candidates: Dict[int, List[Candidate]] = {}
        total_candidates = 0

        for s in section_infos:
            cand_list: List[Candidate] = []

            has_lab = has_lab_for_course(sections, s.course_id)
            has_tut = has_tutorial_for_course(sections, s.course_id)
            dur = duration_slots(s.section_type, has_lab, has_tut)

            if s.course_code in SUN_SAT_COURSES:
                special_days = ["SUN", "SAT"] if s.course_code == "INT401" else ["SUN"]
                starts = [1] if is_lab(s.section_type) else allowed_slots_sparse("SUN", dur)

                for st in starts:
                    cand_list.append(
                        Candidate(
                            sec_id=s.id,
                            days=",".join(special_days),
                            days_list=special_days,
                            start=st,
                            dur=dur,
                            room_id=None,
                            room_code=None,
                        )
                    )
            else:
                room_options = allowed_rooms_for_section(s, rooms)

                if not room_options:
                    print(
                        f"[NO-ROOM] section={s.section_code} type={s.section_type} "
                        f"gender={s.gender_allowed} capacity={s.capacity}"
                    )

                if is_lecture(s.section_type):
                    room_options = room_options[:lecture_limit]
                elif is_tutorial(s.section_type):
                    room_options = room_options[:tutorial_limit]
                elif is_lab(s.section_type):
                    room_options = room_options[:lab_limit]

                if is_lecture(s.section_type):
                    patterns = LECTURE_PATTERNS
                else:
                    patterns = SINGLE_DAYS

                for pattern in patterns:
                    day_list = split_days(pattern)
                    ref_day = day_list[0]

                    if is_lab(s.section_type):
                        starts = allowed_slots_dense(ref_day, dur)
                    else:
                        starts = allowed_slots_sparse(ref_day, dur)

                    for st in starts:
                        for r in room_options:
                            cand_list.append(
                                Candidate(
                                    sec_id=s.id,
                                    days=pattern,
                                    days_list=day_list,
                                    start=st,
                                    dur=dur,
                                    room_id=r.id,
                                    room_code=r.room_code,
                                )
                            )

            candidates[s.id] = cand_list
            total_candidates += len(cand_list)

        print(f"[CP] total candidates={total_candidates}")

        model = cp_model.CpModel()
        x: Dict[tuple[int, int], cp_model.IntVar] = {}

        for sid, cand_list in candidates.items():
            if not cand_list:
                raise ValueError(f"Section {sid} has no candidates.")
            for i in range(len(cand_list)):
                x[(sid, i)] = model.NewBoolVar(f"x_{sid}_{i}")

        for sid, cand_list in candidates.items():
            model.AddAtMostOne(x[(sid, i)] for i in range(len(cand_list)))

        IGNORED_INSTRUCTORS = {"", "TBA", "STAFF", "N/A", "-"}

        # Build optional fixed-size intervals per (candidate, day) and group them
        # by shared resources. Then let CP-SAT enforce non-overlap natively. This
        # replaces the previous O(N^2 * C^2) Python pairwise loop with an O(N*D)
        # grouping pass, where N = sections, C = candidates per section, D = days.
        intervals_by_room_day: Dict[Tuple[int, str], list] = defaultdict(list)
        intervals_by_instr_day: Dict[Tuple[str, str], list] = defaultdict(list)
        presences_by_instr_day: Dict[Tuple[str, str], list] = defaultdict(list)
        # (presence_var, start_slot, end_slot, sec_id) for gap reasoning per instr/day.
        cand_meta_by_instr_day: Dict[Tuple[str, str], list] = defaultdict(list)

        section_by_id = {s.id: s for s in section_infos}

        for sid, cand_list in candidates.items():
            sec = section_by_id[sid]
            instr = norm_code(sec.instructor)
            instr_active = instr not in IGNORED_INSTRUCTORS

            for i, cand in enumerate(cand_list):
                presence = x[(sid, i)]
                # One interval per day this candidate occupies (fixed start/size).
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
                        cand_meta_by_instr_day[(instr, day)].append(
                            (presence, cand.start, cand.start + cand.dur, sid)
                        )

        for ivs in intervals_by_room_day.values():
            if len(ivs) > 1:
                model.AddNoOverlap(ivs)

        for ivs in intervals_by_instr_day.values():
            if len(ivs) > 1:
                model.AddNoOverlap(ivs)

        # Cap the number of section meetings any instructor can teach in a single day.
        # Each candidate contributes at most one presence per day, and at most one
        # candidate per section is chosen, so summing presences gives the count of
        # distinct section meetings scheduled for that instructor on that day.
        if max_sections_per_instructor_per_day > 0:
            for presences in presences_by_instr_day.values():
                if len(presences) > max_sections_per_instructor_per_day:
                    model.Add(sum(presences) <= max_sections_per_instructor_per_day)

        # Limit idle time between consecutive classes for the same instructor on the
        # same day. Many candidates share identical (section, day, start, end) but
        # differ only in the room they propose. For gap reasoning we collapse those
        # duplicates into a single "slot_present" boolean = OR of room candidates,
        # which keeps the constraint count manageable on real-sized inputs.
        slot_present_by_instr_day: Dict[Tuple[str, str], list] = defaultdict(list)
        for (instr, day), metas in cand_meta_by_instr_day.items():
            grouped: Dict[Tuple[int, int, int], list] = defaultdict(list)
            for pres, start, end, sid in metas:
                grouped[(sid, start, end)].append(pres)
            for (sid, start, end), pres_list in grouped.items():
                if len(pres_list) == 1:
                    slot_var = pres_list[0]
                else:
                    slot_var = model.NewBoolVar(
                        f"slot_{instr}_{day}_{sid}_{start}_{end}"
                    )
                    # slot_var ⇔ any of these room-specific candidates selected.
                    model.AddBoolOr(pres_list).OnlyEnforceIf(slot_var)
                    model.AddBoolAnd([p.Not() for p in pres_list]).OnlyEnforceIf(
                        slot_var.Not()
                    )
                slot_present_by_instr_day[(instr, day)].append(
                    (slot_var, start, end, sid)
                )

        gap_pair_constraints = 0
        if max_instructor_gap_slots >= 0:
            for (instr, day), slots in slot_present_by_instr_day.items():
                n = len(slots)
                if n < 2:
                    continue
                slots_sorted = sorted(slots, key=lambda s: (s[1], s[2]))
                for ai in range(n):
                    pres_a, start_a, end_a, sid_a = slots_sorted[ai]
                    for bi in range(ai + 1, n):
                        pres_b, start_b, end_b, sid_b = slots_sorted[bi]
                        if start_b < end_a:
                            continue
                        gap = start_b - end_a
                        if gap <= max_instructor_gap_slots:
                            continue
                        if sid_a == sid_b:
                            continue
                        fillers = [
                            pres_k
                            for (pres_k, start_k, end_k, sid_k) in slots_sorted
                            if sid_k not in (sid_a, sid_b)
                            and start_k >= end_a
                            and end_k <= start_b
                        ]
                        model.AddBoolOr(
                            [pres_a.Not(), pres_b.Not()] + fillers
                        )
                        gap_pair_constraints += 1
        print(f"[CP] instructor gap constraints added: {gap_pair_constraints}")

        model.Maximize(
            sum(x[(sid, i)] for sid, cand_list in candidates.items() for i in range(len(cand_list)))
        )

        validation_error = model.Validate()
        print("[CP] model validation:", validation_error if validation_error else "OK")

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(solver_time_seconds)
        # 0 lets CP-SAT pick a portfolio sized for the available CPU cores.
        solver.parameters.num_search_workers = 0
        solver.parameters.log_search_progress = False

        print(f"[CP] solving (max {solver_time_seconds}s)...")
        status = solver.Solve(model)

        print("[CP] solver status code =", status)
        print("[CP] solver status name =", solver.StatusName(status))

        # Fallback: if the gap-constrained model couldn't be solved in time, retry
        # without the per-instructor gap constraints. This guarantees the user gets
        # a schedule even on slower hardware where the harder model times out.
        if (
            status not in (cp_model.FEASIBLE, cp_model.OPTIMAL)
            and gap_pair_constraints > 0
        ):
            print(
                "[CP] gap-constrained model timed out — retrying without instructor "
                "gap rule so a schedule can still be produced"
            )
            # Rebuild a lighter model without gap constraints by re-running the
            # whole construction, this time forcing the gap limit off.
            return run_ga_schedule(
                lecture_limit=lecture_limit,
                tutorial_limit=tutorial_limit,
                lab_limit=lab_limit,
                solver_time_seconds=solver_time_seconds,
                max_sections_per_instructor_per_day=max_sections_per_instructor_per_day,
                max_instructor_gap_slots=-1,
            )

        if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            print("❌ model did not solve")
            return

        print("✅ solved")

        db.execute(delete(ScheduledMeeting))
        db.flush()

        raw_sections_by_id: Dict[int, Section] = {s.id: s for s in sections}

        for raw in sections:
            raw.days = None
            raw.start_time = None
            raw.end_time = None

        scheduled = 0
        unscheduled_sections: List[str] = []

        for s in section_infos:
            chosen: Candidate | None = None
            for i, cand in enumerate(candidates[s.id]):
                if solver.Value(x[(s.id, i)]) == 1:
                    chosen = cand
                    break

            if chosen is None:
                unscheduled_sections.append(
                    f"{s.id} | {s.course_code} | {s.section_code} | {s.section_type} | {s.instructor or '-'}"
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

        print(f"scheduled: {scheduled}")
        print(f"unscheduled: {len(section_infos) - scheduled}")

        if unscheduled_sections:
            print("[UNSCHEDULED SECTIONS]")
            for item in unscheduled_sections:
                print(item)

    finally:
        db.close()


if __name__ == "__main__":
    run_ga_schedule()