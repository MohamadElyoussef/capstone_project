import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from itertools import combinations
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Course, Enrollment, Room, ScheduledMeeting, Section

WEEK_DAYS = ("MON", "TUE", "WED", "THU", "FRI")
DAY_ORDER = {day: idx for idx, day in enumerate(WEEK_DAYS)}
SECTION_ORDER = {"LECTURE": 0, "LAB": 1, "TUTORIAL": 2}

DEFAULT_EXPECTED_ENROLLMENT = 35

UNSCHEDULED_REASONS = {
    "NO_VALID_TIME_SLOT",
    "NO_ROOM_FITS_CAPACITY",
    "NO_COMPATIBLE_GENDER_ROOM",
    "INSTRUCTOR_DAILY_LIMIT",
    "INSTRUCTOR_SEMESTER_LIMIT",
    "FRIDAY_NOT_ALLOWED_FOR_LECTURE",
    "RULES_BLOCKED",
}

DAY_TOKEN_MAP = {
    "M": "MON",
    "MON": "MON",
    "MONDAY": "MON",
    "T": "TUE",
    "TU": "TUE",
    "TUE": "TUE",
    "TUESDAY": "TUE",
    "W": "WED",
    "WED": "WED",
    "WEDNESDAY": "WED",
    "TH": "THU",
    "THU": "THU",
    "THURSDAY": "THU",
    "F": "FRI",
    "FRI": "FRI",
    "FRIDAY": "FRI",
}

_LAST_UNSCHEDULED_SECTIONS: list[dict[str, Any]] = []
_HAS_GENERATED_SCHEDULE = False


@dataclass(frozen=True)
class _MeetingTemplate:
    day: str
    start_time: time
    end_time: time


@dataclass(frozen=True)
class _PlacedMeeting:
    section_id: int
    course_id: int
    section_code: str
    section_type: str
    instructor: str | None
    day: str
    start_time: time
    end_time: time
    room_id: int
    room_code: str


@dataclass(frozen=True)
class _MeetingSnapshot:
    meeting_id: int
    section_id: int
    course_id: int
    section_code: str
    section_type: str
    instructor: str | None
    day: str
    start_time: time
    end_time: time
    room_id: int
    room_code: str


def _time_from_hm(hours: int, minutes: int) -> time:
    return time(hour=hours, minute=minutes)


def _time_to_str(value: time) -> str:
    return value.strftime("%H:%M")


def _add_minutes(start: time, duration_minutes: int) -> time:
    base = datetime.combine(datetime.min.date(), start)
    return (base + timedelta(minutes=duration_minutes)).time()


def _times_overlap(start_a: time, end_a: time, start_b: time, end_b: time) -> bool:
    return start_a < end_b and start_b < end_a


BREAK_DAYS = frozenset({"TUE", "THU"})
BREAK_START = time(12, 30)
BREAK_END = time(13, 30)


def _meeting_overlaps_break(day: str, start: time, end: time) -> bool:
    return day.upper() in BREAK_DAYS and _times_overlap(start, end, BREAK_START, BREAK_END)


def _template_overlaps_break(template: list["_MeetingTemplate"]) -> bool:
    return any(_meeting_overlaps_break(m.day, m.start_time, m.end_time) for m in template)


def _normalize_instructor(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.upper() in {"TBA", "T.B.A", "TO BE ANNOUNCED"}:
        return None
    return normalized


def _normalize_gender(value: str | None) -> str:
    if value is None:
        return "BOTH"
    normalized = value.strip().upper()
    if normalized in {"M", "F", "BOTH"}:
        return normalized
    return "BOTH"


def _effective_expected_enrollment(value: int | None) -> int:
    if value is None:
        return DEFAULT_EXPECTED_ENROLLMENT
    return value if value > 0 else DEFAULT_EXPECTED_ENROLLMENT


def _parse_days(value: str | None) -> set[str]:
    if not value:
        return set()
    tokens = [token for token in re.split(r"[,/\\\s]+", value.upper()) if token]
    days: set[str] = set()
    for token in tokens:
        days.add(DAY_TOKEN_MAP.get(token, token[:3]))
    return days


def _is_friday_only_preference(section: Section) -> bool:
    days = _parse_days(section.days)
    return bool(days) and days == {"FRI"}


def _room_matches_gender(section_gender: str, room_code: str) -> bool:
    normalized_gender = _normalize_gender(section_gender)
    if normalized_gender == "BOTH":
        return True

    code = room_code.upper()
    if normalized_gender == "F":
        return "F" in code or "B" in code
    if normalized_gender == "M":
        return "M" in code or "B" in code
    return True


def _has_capacity_room(rooms: list[Room], required_seats: int) -> bool:
    return any(room.capacity >= required_seats for room in rooms)


def _has_gender_compatible_room(
    rooms: list[Room], required_seats: int, section_gender: str
) -> bool:
    return any(
        room.capacity >= required_seats and _room_matches_gender(section_gender, room.room_code)
        for room in rooms
    )


def _lecture_duration_minutes(section_types: set[str]) -> int:
    # إذا في LAB بنفس المادة نخلي الليكتشر 60 بدل 90
    return 60 if "LAB" in section_types else 90


def _build_lecture_templates(
    starts: tuple[time, ...], day_a: str, day_b: str, duration_minutes: int
) -> list[list[_MeetingTemplate]]:
    return [
        [
            _MeetingTemplate(
                day=day_a,
                start_time=start,
                end_time=_add_minutes(start, duration_minutes),
            ),
            _MeetingTemplate(
                day=day_b,
                start_time=start,
                end_time=_add_minutes(start, duration_minutes),
            ),
        ]
        for start in starts
    ]


def _lecture_templates(duration_minutes: int) -> list[list[_MeetingTemplate]]:
    monday_wednesday_starts = (
        _time_from_hm(8, 0),
        _time_from_hm(9, 0) if duration_minutes == 60 else _time_from_hm(9, 30),
        _time_from_hm(10, 0) if duration_minutes == 60 else _time_from_hm(11, 0),
        _time_from_hm(11, 0) if duration_minutes == 60 else _time_from_hm(13, 30),
        _time_from_hm(12, 30) if duration_minutes == 60 else _time_from_hm(15, 0),
        _time_from_hm(13, 30) if duration_minutes == 60 else _time_from_hm(16, 30),
        _time_from_hm(14, 30) if duration_minutes == 60 else _time_from_hm(18, 0),
        _time_from_hm(15, 30) if duration_minutes == 60 else _time_from_hm(19, 30),
    )
    tuesday_thursday_starts = (
        _time_from_hm(8, 0),
        _time_from_hm(9, 0) if duration_minutes == 60 else _time_from_hm(9, 30),
        _time_from_hm(10, 0) if duration_minutes == 60 else _time_from_hm(11, 0),
        _time_from_hm(11, 0) if duration_minutes == 60 else _time_from_hm(13, 30),
        _time_from_hm(13, 30) if duration_minutes == 60 else _time_from_hm(15, 0),
        _time_from_hm(14, 30) if duration_minutes == 60 else _time_from_hm(16, 30),
        _time_from_hm(15, 30) if duration_minutes == 60 else _time_from_hm(18, 0),
        _time_from_hm(16, 30) if duration_minutes == 60 else _time_from_hm(19, 30),
    )

    mw = _build_lecture_templates(monday_wednesday_starts, "MON", "WED", duration_minutes)
    tth = _build_lecture_templates(tuesday_thursday_starts, "TUE", "THU", duration_minutes)

    # Interleave MW و TTH حتى ما يعبي MW كله أول
    interleaved: list[list[_MeetingTemplate]] = []
    for i in range(max(len(mw), len(tth))):
        if i < len(mw):
            interleaved.append(mw[i])
        if i < len(tth):
            interleaved.append(tth[i])

    return interleaved


def _single_day_templates(
    days: tuple[str, ...], duration_minutes: int
) -> list[list[_MeetingTemplate]]:
    # Slots موزعة لحتى ما يضل كل شي قبل 12
    starts = (
        _time_from_hm(8, 0),
        _time_from_hm(10, 0),
        _time_from_hm(12, 0),
        _time_from_hm(14, 0),
        _time_from_hm(16, 0),
    )
    templates: list[list[_MeetingTemplate]] = []
    for day in days:
        for start in starts:
            templates.append(
                [
                    _MeetingTemplate(
                        day=day,
                        start_time=start,
                        end_time=_add_minutes(start, duration_minutes),
                    )
                ]
            )
    return templates


def _lab_tutorial_templates() -> list[list[_MeetingTemplate]]:
    # باقي الأيام مثل قبل
    normal_days = ("MON", "TUE", "WED", "THU")

    templates = _single_day_templates(normal_days, 120)

    # الجمعة بس فترتين محددتين
    friday_starts = (
        _time_from_hm(8, 0),
        _time_from_hm(12, 0),
    )

    for start in friday_starts:
        templates.append(
            [
                _MeetingTemplate(
                    day="FRI",
                    start_time=start,
                    end_time=_add_minutes(start, 120),
                )
            ]
        )

    return templates


def _candidate_templates(
    section: Section,
    course_section_types: dict[int, set[str]],
) -> list[list[_MeetingTemplate]]:
    section_type = (section.section_type or "").upper()
    if section_type == "LECTURE":
        duration_minutes = _lecture_duration_minutes(
            course_section_types.get(section.course_id, {section_type})
        )
        templates = _lecture_templates(duration_minutes)
    elif section_type in {"LAB", "TUTORIAL"}:
        templates = _lab_tutorial_templates()
    else:
        return []
    return [t for t in templates if not _template_overlaps_break(t)]


def _select_room(
    *,
    rooms: list[Room],
    required_seats: int,
    section_gender: str,
    day: str,
    start_time: time,
    end_time: time,
    placed_meetings: list[_PlacedMeeting],
) -> Room | None:
    for room in rooms:
        if room.capacity < required_seats:
            continue
        if not _room_matches_gender(section_gender, room.room_code):
            continue
        room_busy = any(
            existing.day == day
            and existing.room_id == room.id
            and _times_overlap(existing.start_time, existing.end_time, start_time, end_time)
            for existing in placed_meetings
        )
        if not room_busy:
            return room
    return None


def _meeting_sort_key(item: _MeetingSnapshot) -> tuple[int, time, str, str]:
    return (
        DAY_ORDER.get(item.day, 99),
        item.start_time,
        item.room_code,
        item.section_code,
    )


def _build_unscheduled_payload(section: Section, course: Course, reason: str) -> dict[str, Any]:
    resolved_reason = reason if reason in UNSCHEDULED_REASONS else "RULES_BLOCKED"
    return {
        "section_id": section.id,
        "section_code": section.section_code,
        "section_type": (section.section_type or "").upper(),
        "course_code": course.code,
        "course_name": course.name,
        "instructor": _normalize_instructor(section.instructor) or "",
        "gender_allowed": _normalize_gender(section.gender_allowed),
        "reason": resolved_reason,
    }


def _required_seats(section: Section, enrollment_counts: dict[int, int]) -> int:
    current_enrolled_count = enrollment_counts.get(section.id, 0)
    expected_enrollment = _effective_expected_enrollment(section.expected_enrollment)
    return max(current_enrolled_count, expected_enrollment)


def _derive_unscheduled_reason(
    *,
    template_count: int,
    semester_limit_blocks: int,
    daily_limit_blocks: int,
    slot_blocks: int,
) -> str:
    if template_count > 0 and semester_limit_blocks == template_count:
        return "INSTRUCTOR_SEMESTER_LIMIT"
    if template_count > 0 and daily_limit_blocks == template_count:
        return "INSTRUCTOR_DAILY_LIMIT"
    if slot_blocks > 0:
        return "NO_VALID_TIME_SLOT"
    if semester_limit_blocks > 0 and daily_limit_blocks > 0:
        return "RULES_BLOCKED"
    if semester_limit_blocks > 0:
        return "INSTRUCTOR_SEMESTER_LIMIT"
    if daily_limit_blocks > 0:
        return "INSTRUCTOR_DAILY_LIMIT"
    return "RULES_BLOCKED"


def get_last_unscheduled_sections() -> list[dict[str, Any]]:
    if not _HAS_GENERATED_SCHEDULE:
        return []
    return [item.copy() for item in _LAST_UNSCHEDULED_SECTIONS]


def _load_scheduled_meetings(db: Session) -> list[_PlacedMeeting]:
    rows = db.execute(
        select(ScheduledMeeting, Section, Room)
        .join(Section, Section.id == ScheduledMeeting.section_id)
        .join(Room, Room.id == ScheduledMeeting.room_id)
    ).all()
    return [
        _PlacedMeeting(
            section_id=row.Section.id,
            course_id=row.Section.course_id,
            section_code=row.Section.section_code,
            section_type=(row.Section.section_type or "").upper(),
            instructor=_normalize_instructor(row.Section.instructor),
            day=row.ScheduledMeeting.day,
            start_time=row.ScheduledMeeting.start_time,
            end_time=row.ScheduledMeeting.end_time,
            room_id=row.Room.id,
            room_code=row.Room.room_code,
        )
        for row in rows
    ]


def _build_instructor_maps(
    meetings: list[_PlacedMeeting],
) -> tuple[dict[str, set[int]], dict[str, dict[str, set[int]]]]:
    instructor_courses: dict[str, set[int]] = defaultdict(set)
    instructor_courses_per_day: dict[str, dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )

    for meeting in meetings:
        if meeting.instructor is None:
            continue
        instructor_courses[meeting.instructor].add(meeting.course_id)
        instructor_courses_per_day[meeting.instructor][meeting.day].add(meeting.course_id)

    return instructor_courses, instructor_courses_per_day


def _format_option(day: str, start_time: time, end_time: time, room: Room) -> dict[str, Any]:
    return {
        "day": day,
        "start_time": _time_to_str(start_time),
        "end_time": _time_to_str(end_time),
        "room_id": room.id,
        "room_code": room.room_code,
    }


def _dedupe_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, int]] = set()
    deduped: list[dict[str, Any]] = []
    for option in options:
        key = (option["day"], option["start_time"], option["end_time"], option["room_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    return deduped


def _find_room_time_options(
    *,
    section: Section,
    templates: list[list[_MeetingTemplate]],
    rooms: list[Room],
    required_seats: int,
    section_gender: str,
    instructor: str | None,
    placed_meetings: list[_PlacedMeeting],
    instructor_courses: dict[str, set[int]],
    instructor_courses_per_day: dict[str, dict[str, set[int]]],
    max_options: int,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for template in templates:
        if instructor is not None:
            taught_courses = instructor_courses.get(instructor, set())
            if section.course_id not in taught_courses and len(taught_courses) >= 4:
                continue
            day_limits_ok = all(
                section.course_id in instructor_courses_per_day[instructor][candidate.day]
                or len(instructor_courses_per_day[instructor][candidate.day]) < 3
                for candidate in template
            )
            if not day_limits_ok:
                continue

        provisional: list[_PlacedMeeting] = []
        template_valid = True
        template_options: list[dict[str, Any]] = []

        for candidate in template:
            if instructor is not None:
                instructor_busy = any(
                    existing.day == candidate.day
                    and existing.instructor == instructor
                    and _times_overlap(
                        existing.start_time,
                        existing.end_time,
                        candidate.start_time,
                        candidate.end_time,
                    )
                    for existing in placed_meetings + provisional
                )
                if instructor_busy:
                    template_valid = False
                    break

            room = _select_room(
                rooms=rooms,
                required_seats=required_seats,
                section_gender=section_gender,
                day=candidate.day,
                start_time=candidate.start_time,
                end_time=candidate.end_time,
                placed_meetings=placed_meetings + provisional,
            )
            if room is None:
                template_valid = False
                break

            provisional.append(
                _PlacedMeeting(
                    section_id=section.id,
                    course_id=section.course_id,
                    section_code=section.section_code,
                    section_type=(section.section_type or "").upper(),
                    instructor=instructor,
                    day=candidate.day,
                    start_time=candidate.start_time,
                    end_time=candidate.end_time,
                    room_id=room.id,
                    room_code=room.room_code,
                )
            )
            template_options.append(
                _format_option(
                    day=candidate.day,
                    start_time=candidate.start_time,
                    end_time=candidate.end_time,
                    room=room,
                )
            )

        if not template_valid:
            continue

        options.extend(template_options)
        if len(options) >= max_options:
            break

    return _dedupe_options(options)[:max_options]


def get_unscheduled_suggestions(db: Session) -> list[dict[str, Any]]:
    unscheduled_sections = get_last_unscheduled_sections()
    if not unscheduled_sections:
        return []

    section_ids = [item["section_id"] for item in unscheduled_sections]
    section_rows = db.execute(
        select(Section, Course)
        .join(Course, Course.id == Section.course_id)
        .where(Section.id.in_(section_ids))
    ).all()
    section_map: dict[int, tuple[Section, Course]] = {
        row.Section.id: (row.Section, row.Course) for row in section_rows
    }

    rooms = db.scalars(
        select(Room).order_by(
            Room.capacity.asc(),
            Room.building_code.asc(),
            Room.room_code.asc(),
            Room.id.asc(),
        )
    ).all()
    enrollment_counts = {
        int(section_id): int(count)
        for section_id, count in db.execute(
            select(Enrollment.section_id, func.count(Enrollment.id)).group_by(Enrollment.section_id)
        ).all()
    }

    all_sections = db.scalars(select(Section)).all()
    course_section_types: dict[int, set[str]] = defaultdict(set)
    for section in all_sections:
        course_section_types[section.course_id].add((section.section_type or "").upper())

    placed_meetings = _load_scheduled_meetings(db)
    instructor_courses, instructor_courses_per_day = _build_instructor_maps(placed_meetings)

    suggestions_payload: list[dict[str, Any]] = []
    for unscheduled in unscheduled_sections:
        section_id = unscheduled["section_id"]
        section_tuple = section_map.get(section_id)
        if section_tuple is None:
            continue

        section, _course = section_tuple
        reason = unscheduled["reason"]
        templates = _candidate_templates(section, course_section_types)
        section_gender = _normalize_gender(section.gender_allowed)
        required_seats = _required_seats(section, enrollment_counts)
        current_instructor = _normalize_instructor(section.instructor)

        options = _find_room_time_options(
            section=section,
            templates=templates,
            rooms=rooms,
            required_seats=required_seats,
            section_gender=section_gender,
            instructor=current_instructor,
            placed_meetings=placed_meetings,
            instructor_courses=instructor_courses,
            instructor_courses_per_day=instructor_courses_per_day,
            max_options=10,
        )

        if options:
            suggestion_entries = [
                {
                    "type": "SLOT_AVAILABLE",
                    "message": (
                        f"Move to {opt['day']} {opt['start_time']}–{opt['end_time']}"
                        f" in room {opt['room_code']}"
                    ),
                    "payload": opt,
                }
                for opt in options
            ]
        else:
            reason_messages: dict[str, str] = {
                "NO_VALID_TIME_SLOT": "All time slots conflict with existing meetings. Consider reassigning the instructor or clearing conflicting sections.",
                "INSTRUCTOR_SEMESTER_LIMIT": "Instructor has reached the 4-course semester limit. Assign a different instructor.",
                "INSTRUCTOR_DAILY_LIMIT": "Instructor already teaches 3 courses on every available day. Reassign the instructor.",
                "NO_ROOM_FITS_CAPACITY": "No room is large enough for the required enrollment. Reduce enrollment or add a larger room.",
                "NO_COMPATIBLE_GENDER_ROOM": "No gender-compatible room is available. Add a suitable room or change the section gender setting.",
                "FRIDAY_NOT_ALLOWED_FOR_LECTURE": "Lectures cannot be placed on Friday. Change the section type or days.",
            }
            suggestion_entries = [
                {
                    "type": "NO_SLOTS",
                    "message": reason_messages.get(
                        reason,
                        "No valid slot found. Review instructor limits, room capacity, and existing schedule conflicts.",
                    ),
                    "payload": {},
                }
            ]

        suggestions_payload.append(
            {
                "section_id": section.id,
                "section_code": section.section_code,
                "reason": reason,
                "suggestions": suggestion_entries,
            }
        )

    return suggestions_payload


def _template_is_am(template: list[_MeetingTemplate]) -> bool:
    start = template[0].start_time
    return start < time(12, 0)


def _template_is_pm(template: list[_MeetingTemplate]) -> bool:
    start = template[0].start_time
    return time(13, 0) <= start <= time(16, 30)


def _prefer_templates(
    templates: list[list[_MeetingTemplate]],
    prefer: str | None,
) -> list[list[_MeetingTemplate]]:
    if prefer == "AM":
        am = [t for t in templates if _template_is_am(t)]
        rest = [t for t in templates if t not in am]
        return am + rest
    if prefer == "PM":
        pm = [t for t in templates if _template_is_pm(t)]
        rest = [t for t in templates if t not in pm]
        return pm + rest
    return templates


def _time_bucket(start: time) -> str:
    if start < time(12, 0):
        return "AM"
    if start < time(16, 30):
        return "PM"
    return "EV"


def _template_days(template: list[_MeetingTemplate]) -> tuple[str, ...]:
    return tuple(item.day for item in template)


def _template_time_bucket(template: list[_MeetingTemplate]) -> str:
    return _time_bucket(template[0].start_time)


def _score_template(
    template: list[_MeetingTemplate],
    day_load: dict[str, int],
    time_load: dict[str, int],
    prefer: str | None,
) -> tuple[int, int, int, time]:
    days = _template_days(template)
    day_score = sum(day_load.get(d, 0) for d in days)

    bucket = _template_time_bucket(template)
    time_score = time_load.get(bucket, 0)

    prefer_penalty = 0
    if prefer == "AM" and bucket != "AM":
        prefer_penalty = 1
    elif prefer == "PM" and bucket != "PM":
        prefer_penalty = 1

    return (day_score, time_score, prefer_penalty, template[0].start_time)


def _sort_templates_smart(
    templates: list[list[_MeetingTemplate]],
    day_load: dict[str, int],
    time_load: dict[str, int],
    prefer: str | None,
) -> list[list[_MeetingTemplate]]:
    return sorted(templates, key=lambda t: _score_template(t, day_load, time_load, prefer))


def _sort_templates_minimize_gap(
    templates: list[list[_MeetingTemplate]],
    instructor: str,
    placed_meetings: list[_PlacedMeeting],
) -> list[list[_MeetingTemplate]]:
    day_slots: dict[str, list[tuple[time, time]]] = defaultdict(list)
    for m in placed_meetings:
        if m.instructor == instructor:
            day_slots[m.day].append((m.start_time, m.end_time))

    if not any(day_slots.values()):
        return templates

    base_date = datetime.min.date()

    def gap_minutes(template: list[_MeetingTemplate]) -> int:
        total = 0
        for cand in template:
            for ex_s, ex_e in day_slots.get(cand.day, []):
                cand_s = datetime.combine(base_date, cand.start_time)
                cand_e = datetime.combine(base_date, cand.end_time)
                ex_start = datetime.combine(base_date, ex_s)
                ex_end = datetime.combine(base_date, ex_e)
                if cand_s >= ex_end:
                    total += int((cand_s - ex_end).total_seconds() // 60)
                elif ex_start >= cand_e:
                    total += int((ex_start - cand_e).total_seconds() // 60)
        return total

    return sorted(templates, key=gap_minutes)


def generate_full_schedule(db: Session) -> dict[str, Any]:
    global _HAS_GENERATED_SCHEDULE
    global _LAST_UNSCHEDULED_SECTIONS

    db.execute(delete(ScheduledMeeting))
    db.flush()

    rooms = db.scalars(
        select(Room).order_by(
            Room.capacity.asc(),
            Room.building_code.asc(),
            Room.room_code.asc(),
            Room.id.asc(),
        )
    ).all()

    section_rows = db.execute(
        select(Section, Course).join(Course, Course.id == Section.course_id)
    ).all()

    enrollment_counts = {
        int(section_id): int(count)
        for section_id, count in db.execute(
            select(Enrollment.section_id, func.count(Enrollment.id)).group_by(Enrollment.section_id)
        ).all()
    }

    section_rows.sort(
        key=lambda row: (
            SECTION_ORDER.get((row.Section.section_type or "").upper(), 99),
            row.Section.course_id,
            row.Section.section_code,
            row.Section.id,
        )
    )

    course_section_types: dict[int, set[str]] = defaultdict(set)
    for row in section_rows:
        section = row.Section
        course_section_types[section.course_id].add((section.section_type or "").upper())

    # Lecture preference per course:
    # 1 lecture: prefer AM
    # 2 lectures: first AM, second PM
    lecture_sections_by_course: dict[int, list[Section]] = defaultdict(list)
    for row in section_rows:
        s = row.Section
        if (s.section_type or "").upper() == "LECTURE":
            lecture_sections_by_course[s.course_id].append(s)

    for course_id in lecture_sections_by_course:
        lecture_sections_by_course[course_id].sort(key=lambda s: (s.section_code or "", s.id))

    lecture_preference_by_section_id: dict[int, str] = {}
    for _course_id, lectures in lecture_sections_by_course.items():
        if len(lectures) == 1:
            lecture_preference_by_section_id[lectures[0].id] = "AM"
        elif len(lectures) == 2:
            lecture_preference_by_section_id[lectures[0].id] = "AM"
            lecture_preference_by_section_id[lectures[1].id] = "PM"
        else:
            for idx, lec in enumerate(lectures):
                lecture_preference_by_section_id[lec.id] = "AM" if idx % 2 == 0 else "PM"

    # Friday balancing for LAB/TUTORIAL, نخلي نسبة بسيطة فقط
    lab_tut_total = sum(
        1
        for row in section_rows
        if (row.Section.section_type or "").upper() in {"LAB", "TUTORIAL"}
    )
    friday_target = max(1, int(lab_tut_total * 0.05))
    friday_lab_tut_used = 0

    placed_meetings: list[_PlacedMeeting] = []
    instructor_courses: dict[str, set[int]] = defaultdict(set)
    instructor_courses_per_day: dict[str, dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    unscheduled_sections: list[dict[str, Any]] = []

    day_load: dict[str, int] = {day: 0 for day in WEEK_DAYS}
    time_load: dict[str, int] = {"AM": 0, "PM": 0, "EV": 0}

    scheduled_sections = 0
    for row in section_rows:
        section: Section = row.Section
        course: Course = row.Course

        section_type = (section.section_type or "").upper()

        templates = _candidate_templates(section, course_section_types)

        # LAB/TUTORIAL: خلّي الجمعة خيار موجود بس آخر شي بعد ما نوصل الهدف
        if section_type in {"LAB", "TUTORIAL"} and templates:
            friday_templates = [t for t in templates if all(x.day == "FRI" for x in t)]
            non_friday_templates = [t for t in templates if not all(x.day == "FRI" for x in t)]
            if friday_lab_tut_used < friday_target:
                templates = friday_templates + non_friday_templates
            else:
                templates = non_friday_templates + friday_templates

        prefer = None
        if section_type == "LECTURE":
            prefer = lecture_preference_by_section_id.get(section.id)

        if templates:
            templates = _sort_templates_smart(templates, day_load, time_load, prefer)

        if instructor is not None and templates:
            templates = _sort_templates_minimize_gap(templates, instructor, placed_meetings)

        section_gender = _normalize_gender(section.gender_allowed)
        current_enrolled_count = enrollment_counts.get(section.id, 0)
        expected_enrollment = _effective_expected_enrollment(section.expected_enrollment)
        required_seats = max(current_enrolled_count, expected_enrollment)

        if section_type == "LECTURE" and _is_friday_only_preference(section):
            unscheduled_sections.append(
                _build_unscheduled_payload(section, course, "FRIDAY_NOT_ALLOWED_FOR_LECTURE")
            )
            continue

        if not templates:
            unscheduled_sections.append(_build_unscheduled_payload(section, course, "RULES_BLOCKED"))
            continue

        if not _has_capacity_room(rooms, required_seats):
            unscheduled_sections.append(
                _build_unscheduled_payload(section, course, "NO_ROOM_FITS_CAPACITY")
            )
            continue

        if not _has_gender_compatible_room(rooms, required_seats, section_gender):
            unscheduled_sections.append(
                _build_unscheduled_payload(section, course, "NO_COMPATIBLE_GENDER_ROOM")
            )
            continue

        instructor = _normalize_instructor(section.instructor)
        assigned = False
        semester_limit_blocks = 0
        daily_limit_blocks = 0
        slot_blocks = 0

        for template in templates:
            if instructor is not None:
                taught_courses = instructor_courses[instructor]
                if section.course_id not in taught_courses and len(taught_courses) >= 4:
                    semester_limit_blocks += 1
                    continue
                day_limits_ok = all(
                    section.course_id in instructor_courses_per_day[instructor][candidate.day]
                    or len(instructor_courses_per_day[instructor][candidate.day]) < 3
                    for candidate in template
                )
                if not day_limits_ok:
                    daily_limit_blocks += 1
                    continue

            provisional: list[_PlacedMeeting] = []
            template_valid = True

            for candidate in template:
                if instructor is not None:
                    instructor_busy = any(
                        existing.day == candidate.day
                        and existing.instructor == instructor
                        and _times_overlap(
                            existing.start_time,
                            existing.end_time,
                            candidate.start_time,
                            candidate.end_time,
                        )
                        for existing in placed_meetings + provisional
                    )
                    if instructor_busy:
                        template_valid = False
                        slot_blocks += 1
                        break

                room = _select_room(
                    rooms=rooms,
                    required_seats=required_seats,
                    section_gender=section_gender,
                    day=candidate.day,
                    start_time=candidate.start_time,
                    end_time=candidate.end_time,
                    placed_meetings=placed_meetings + provisional,
                )
                if room is None:
                    template_valid = False
                    slot_blocks += 1
                    break

                provisional.append(
                    _PlacedMeeting(
                        section_id=section.id,
                        course_id=section.course_id,
                        section_code=section.section_code,
                        section_type=section_type,
                        instructor=instructor,
                        day=candidate.day,
                        start_time=candidate.start_time,
                        end_time=candidate.end_time,
                        room_id=room.id,
                        room_code=room.room_code,
                    )
                )

            if not template_valid:
                continue

            placed_meetings.extend(provisional)
            if instructor is not None:
                instructor_courses[instructor].add(section.course_id)
                for candidate in provisional:
                    instructor_courses_per_day[instructor][candidate.day].add(section.course_id)

            for candidate in provisional:
                db.add(
                    ScheduledMeeting(
                        section_id=candidate.section_id,
                        day=candidate.day,
                        start_time=candidate.start_time,
                        end_time=candidate.end_time,
                        room_id=candidate.room_id,
                    )
                )

            # تحديث أحمال الأيام والوقت
            for m in provisional:
                if m.day in day_load:
                    day_load[m.day] += 1
                bucket = _time_bucket(m.start_time)
                time_load[bucket] += 1

            assigned = True

            if section_type in {"LAB", "TUTORIAL"} and all(m.day == "FRI" for m in provisional):
                friday_lab_tut_used += 1

            break

        if assigned:
            scheduled_sections += 1
            continue

        unscheduled_sections.append(
            _build_unscheduled_payload(
                section,
                course,
                _derive_unscheduled_reason(
                    template_count=len(templates),
                    semester_limit_blocks=semester_limit_blocks,
                    daily_limit_blocks=daily_limit_blocks,
                    slot_blocks=slot_blocks,
                ),
            )
        )

    db.commit()
    _LAST_UNSCHEDULED_SECTIONS = [item.copy() for item in unscheduled_sections]
    _HAS_GENERATED_SCHEDULE = True

    conflicts_found = len(unscheduled_sections)
    return {
        "total_sections": len(section_rows),
        "scheduled_sections": scheduled_sections,
        "conflicts_found": conflicts_found,
        "unscheduled_sections": unscheduled_sections,
    }


def get_weekly_schedule(db: Session) -> dict[str, list[dict[str, Any]]]:
    rows = db.execute(
        select(ScheduledMeeting, Section, Course, Room)
        .join(Section, Section.id == ScheduledMeeting.section_id)
        .join(Course, Course.id == Section.course_id)
        .join(Room, Room.id == ScheduledMeeting.room_id)
    ).all()

    schedule: dict[str, list[dict[str, Any]]] = {day: [] for day in WEEK_DAYS}
    for row in rows:
        meeting: ScheduledMeeting = row.ScheduledMeeting
        section: Section = row.Section
        course: Course = row.Course
        room: Room = row.Room
        if meeting.day not in WEEK_DAYS:
            continue
        schedule[meeting.day].append(
            {
                "meeting_id": meeting.id,
                "section_id": section.id,
                "section_code": section.section_code,
                "section_type": (section.section_type or "").upper(),
                "course_id": course.id,
                "course_code": course.code,
                "course_name": course.name,
                "instructor": _normalize_instructor(section.instructor),
                "room_id": room.id,
                "room_code": room.room_code,
                "day": meeting.day,
                "start_time": _time_to_str(meeting.start_time),
                "end_time": _time_to_str(meeting.end_time),
            }
        )

    for day in WEEK_DAYS:
        schedule[day].sort(
            key=lambda item: (
                item["start_time"],
                item["room_code"],
                item["section_code"],
            )
        )

    return {day: schedule.get(day, []) for day in WEEK_DAYS}


def detect_schedule_conflicts(db: Session) -> dict[str, list[dict[str, Any]]]:
    rows = db.execute(
        select(ScheduledMeeting, Section, Room)
        .join(Section, Section.id == ScheduledMeeting.section_id)
        .join(Room, Room.id == ScheduledMeeting.room_id)
    ).all()

    meetings: list[_MeetingSnapshot] = [
        _MeetingSnapshot(
            meeting_id=row.ScheduledMeeting.id,
            section_id=row.Section.id,
            course_id=row.Section.course_id,
            section_code=row.Section.section_code,
            section_type=(row.Section.section_type or "").upper(),
            instructor=_normalize_instructor(row.Section.instructor),
            day=row.ScheduledMeeting.day,
            start_time=row.ScheduledMeeting.start_time,
            end_time=row.ScheduledMeeting.end_time,
            room_id=row.Room.id,
            room_code=row.Room.room_code,
        )
        for row in rows
    ]
    meetings.sort(key=_meeting_sort_key)

    meetings_by_day: dict[str, list[_MeetingSnapshot]] = defaultdict(list)
    for meeting in meetings:
        meetings_by_day[meeting.day].append(meeting)

    room_conflicts: list[dict[str, Any]] = []
    instructor_conflicts: list[dict[str, Any]] = []

    for day in WEEK_DAYS:
        day_meetings = meetings_by_day.get(day, [])
        for first, second in combinations(day_meetings, 2):
            if not _times_overlap(
                first.start_time, first.end_time, second.start_time, second.end_time
            ):
                continue

            overlap_start = max(first.start_time, second.start_time)
            overlap_end = min(first.end_time, second.end_time)

            if first.room_id == second.room_id:
                room_conflicts.append(
                    {
                        "day": day,
                        "overlap_start": _time_to_str(overlap_start),
                        "overlap_end": _time_to_str(overlap_end),
                        "room_id": first.room_id,
                        "room_code": first.room_code,
                        "first_meeting_id": first.meeting_id,
                        "second_meeting_id": second.meeting_id,
                        "first_section_id": first.section_id,
                        "first_section_code": first.section_code,
                        "second_section_id": second.section_id,
                        "second_section_code": second.section_code,
                    }
                )

            if first.instructor is not None and first.instructor == second.instructor:
                instructor_conflicts.append(
                    {
                        "day": day,
                        "overlap_start": _time_to_str(overlap_start),
                        "overlap_end": _time_to_str(overlap_end),
                        "instructor": first.instructor,
                        "first_meeting_id": first.meeting_id,
                        "second_meeting_id": second.meeting_id,
                        "first_section_id": first.section_id,
                        "first_section_code": first.section_code,
                        "second_section_id": second.section_id,
                        "second_section_code": second.section_code,
                    }
                )

    return {
        "room_conflicts": room_conflicts,
        "instructor_conflicts": instructor_conflicts,
    }