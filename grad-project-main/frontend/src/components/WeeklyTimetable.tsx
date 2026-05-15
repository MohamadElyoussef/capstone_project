import { useEffect, useMemo, useState } from "react";
import {
  type AdminScheduleMeeting,
  getAdminSchedule,
} from "../api/client";

interface WeeklyTimetableProps {
  refreshKey?: number;
  title?: string;
  showBreak?: boolean;
}

interface TimetableMeeting {
  id: string;
  courseCode: string;
  courseName: string;
  sectionCode: string;
  instructor: string | null;
  day: string;
  startTime: string;
  endTime: string;
  roomCode: string | null;
}

interface PositionedMeeting extends TimetableMeeting {
  clippedStart: number;
  clippedEnd: number;
  lane: number;
  totalLanes: number;
}

interface ExpandedCard {
  meeting: PositionedMeeting;
  anchorRect: DOMRect;
}

const DAYS = ["MON", "TUE", "WED", "THU", "FRI"] as const;
const DAY_LABELS: Record<(typeof DAYS)[number], string> = {
  MON: "Monday",
  TUE: "Tuesday",
  WED: "Wednesday",
  THU: "Thursday",
  FRI: "Friday",
};
const START_HOUR = 8;
const END_HOUR = 20;
const FRIDAY_END_HOUR = 13;
const START_MINUTES = START_HOUR * 60;
const END_MINUTES = END_HOUR * 60;
const FRIDAY_END_MINUTES = FRIDAY_END_HOUR * 60;
const PX_PER_MINUTE = 1.5;
const HOUR_HEIGHT = 60 * PX_PER_MINUTE;
const TOTAL_HEIGHT = (END_MINUTES - START_MINUTES) * PX_PER_MINUTE;
const FRIDAY_HEIGHT = (FRIDAY_END_MINUTES - START_MINUTES) * PX_PER_MINUTE;

const BREAK_DAYS = new Set(["TUE", "THU"]);
const BREAK_START_MINUTES = 12 * 60 + 30;
const BREAK_END_MINUTES = 13 * 60 + 30;
const BREAK_TOP = (BREAK_START_MINUTES - START_MINUTES) * PX_PER_MINUTE;
const BREAK_HEIGHT = (BREAK_END_MINUTES - BREAK_START_MINUTES) * PX_PER_MINUTE;

interface CourseColor { bg: string; border: string; text: string }

function hueToColor(hue: number): CourseColor {
  return {
    bg: `hsl(${hue}, 60%, 14%)`,
    border: `hsl(${hue}, 70%, 52%)`,
    text: `hsl(${hue}, 80%, 82%)`,
  };
}

function buildColorMap(codes: string[]): Record<string, CourseColor> {
  const sorted = [...new Set(codes)].sort();
  const map: Record<string, CourseColor> = {};
  sorted.forEach((code, i) => {
    const hue = (i * 137.508) % 360;
    map[code] = hueToColor(hue);
  });
  return map;
}

function parseMinutes(value: string): number | null {
  const match = /^(\d{2}):(\d{2})/.exec(value);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function flattenSchedule(schedule: Record<string, AdminScheduleMeeting[]>): TimetableMeeting[] {
  const meetings: TimetableMeeting[] = [];
  for (const [day, items] of Object.entries(schedule)) {
    for (const item of items) {
      meetings.push({
        id: String(item.meeting_id),
        courseCode: item.course_code,
        courseName: item.course_name,
        sectionCode: item.section_code,
        instructor: item.instructor,
        day: day.toUpperCase(),
        startTime: item.start_time,
        endTime: item.end_time,
        roomCode: item.room_code ?? null,
      });
    }
  }
  return meetings;
}

function assignLanes(raw: TimetableMeeting[], dayEnd: number): PositionedMeeting[] {
  const clipped = raw
    .map((m) => {
      const start = parseMinutes(m.startTime);
      const end = parseMinutes(m.endTime);
      if (start === null || end === null || end <= start) return null;
      const cs = Math.max(start, START_MINUTES);
      const ce = Math.min(end, dayEnd);
      if (ce <= cs) return null;
      return { ...m, clippedStart: cs, clippedEnd: ce };
    })
    .filter((m): m is NonNullable<typeof m> => m !== null);

  clipped.sort((a, b) => a.clippedStart - b.clippedStart || a.clippedEnd - b.clippedEnd);

  const laneEnds: number[] = [];
  const withLane = clipped.map((m) => {
    let lane = laneEnds.findIndex((end) => end <= m.clippedStart);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(m.clippedEnd);
    } else {
      laneEnds[lane] = m.clippedEnd;
    }
    return { ...m, lane, totalLanes: 0 };
  });

  const n = withLane.length;
  const totalLanesFor: number[] = new Array(n).fill(1);
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (withLane[j].clippedStart >= withLane[i].clippedEnd) break;
      const groupMax = Math.max(withLane[i].lane, withLane[j].lane) + 1;
      totalLanesFor[i] = Math.max(totalLanesFor[i], groupMax);
      totalLanesFor[j] = Math.max(totalLanesFor[j], groupMax);
    }
  }

  return withLane.map((m, idx) => ({ ...m, totalLanes: totalLanesFor[idx] }));
}

const HOUR_LABELS: string[] = [];
for (let h = START_HOUR; h <= END_HOUR; h++) {
  const ampm = h < 12 ? "AM" : "PM";
  const label = h <= 12 ? h : h - 12;
  HOUR_LABELS.push(`${label}${ampm}`);
}

function shortTime(t: string) {
  return t.slice(0, 5);
}

function DetailCard({ card, colorMap }: { card: ExpandedCard; colorMap: Record<string, CourseColor> }) {
  const { meeting, anchorRect } = card;
  const color = colorMap[meeting.courseCode] ?? hueToColor(0);

  const viewportW = window.innerWidth;
  const popupW = 220;
  const gap = 10;

  let left = anchorRect.right + gap;
  if (left + popupW > viewportW - 12) {
    left = anchorRect.left - popupW - gap;
  }
  if (left < 8) left = 8;

  let top = anchorRect.top;
  const popupH = 190;
  if (top + popupH > window.innerHeight - 12) {
    top = window.innerHeight - popupH - 12;
  }
  if (top < 8) top = 8;

  return (
    <div
      style={{
        position: "fixed",
        top,
        left,
        width: popupW,
        zIndex: 9999,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          background: "#0f172a",
          border: `1px solid ${color.border}`,
          borderRadius: "0.6rem",
          boxShadow: `0 8px 32px rgba(0,0,0,0.7), 0 0 0 1px ${color.border}33`,
          overflow: "hidden",
        }}
      >
        <div style={{ background: color.bg, borderBottom: `1px solid ${color.border}40`, padding: "8px 12px" }}>
          <div style={{ color: color.text, fontWeight: 700, fontSize: "0.8rem" }}>{meeting.courseCode}</div>
          <div style={{ color: "#94a3b8", fontSize: "0.68rem", marginTop: 2, lineHeight: 1.3 }}>{meeting.courseName}</div>
        </div>
        <div style={{ padding: "8px 12px", display: "grid", gap: "5px" }}>
          {[
            { label: "Section", value: meeting.sectionCode },
            { label: "Time", value: `${shortTime(meeting.startTime)} – ${shortTime(meeting.endTime)}` },
            { label: "Day", value: DAY_LABELS[meeting.day as keyof typeof DAY_LABELS] ?? meeting.day },
            ...(meeting.roomCode ? [{ label: "Room", value: meeting.roomCode }] : []),
            ...(meeting.instructor ? [{ label: "Instructor", value: meeting.instructor }] : []),
          ].map(({ label, value }) => (
            <div key={label} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
              <span style={{ color: "#475569", fontSize: "0.62rem", minWidth: 54 }}>{label}</span>
              <span style={{ color: "#cbd5e1", fontSize: "0.7rem", fontWeight: 500 }}>{value}</span>
            </div>
          ))}
        </div>
        <div style={{ padding: "6px 12px", borderTop: "1px solid #1e293b" }}>
          <span style={{ color: "#475569", fontSize: "0.6rem" }}>Move mouse away to close</span>
        </div>
      </div>
    </div>
  );
}

export function WeeklyTimetable({
  refreshKey = 0,
  title,
  showBreak = true,
}: WeeklyTimetableProps) {
  const [meetings, setMeetings] = useState<TimetableMeeting[]>([]);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [expandedCard, setExpandedCard] = useState<ExpandedCard | null>(null);

  const colorMap = useMemo(
    () => buildColorMap(meetings.map((m) => m.courseCode)),
    [meetings],
  );

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setNotice(null);
      try {
        const schedule = await getAdminSchedule();
        if (!cancelled) setMeetings(flattenSchedule(schedule));
      } catch {
        if (!cancelled) {
          setMeetings([]);
          setNotice("Unable to load timetable.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [refreshKey]);

  const dayMeetings = useMemo(() => {
    const bucket: Record<string, PositionedMeeting[]> = {};
    for (const day of DAYS) {
      const dayEnd = day === "FRI" ? FRIDAY_END_MINUTES : END_MINUTES;
      bucket[day] = assignLanes(
        meetings.filter((m) => m.day === day),
        dayEnd,
      );
    }
    return bucket;
  }, [meetings]);

  return (
    <div className="bg-slate-800/60 backdrop-blur-md border border-slate-700/50 rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-300">{title ?? "Weekly Timetable"}</h3>
        {loading && <span className="text-xs text-slate-500 animate-pulse">Loading…</span>}
      </div>

      {notice && (
        <div className="text-xs text-amber-300 bg-amber-900/20 border border-amber-700/40 rounded-lg px-3 py-2">
          {notice}
        </div>
      )}

      <div className="overflow-x-auto">
        <div style={{ display: "grid", gridTemplateColumns: "52px repeat(5, minmax(130px, 1fr))", minWidth: 720 }}>
          <div style={{ paddingTop: 32 }}>
            {HOUR_LABELS.map((label) => (
              <div
                key={label}
                style={{ height: HOUR_HEIGHT, display: "flex", alignItems: "flex-start", justifyContent: "flex-end", paddingRight: 8 }}
                className="text-slate-500 text-xs"
              >
                {label}
              </div>
            ))}
          </div>

          {DAYS.map((day) => {
            const isFriday = day === "FRI";
            const bodyHeight = isFriday ? FRIDAY_HEIGHT : TOTAL_HEIGHT;
            const slots = dayMeetings[day];
            return (
              <div key={day} className="flex flex-col">
                <div
                  className="text-center text-xs font-semibold text-sky-300 bg-slate-700/60 border-b border-slate-600/50 rounded-t-lg"
                  style={{ height: 32, display: "flex", alignItems: "center", justifyContent: "center" }}
                >
                  {DAY_LABELS[day]}
                </div>
                <div
                  className="relative rounded-b-lg border border-slate-700/40 bg-slate-900/40"
                  style={{ height: bodyHeight, overflow: "visible" }}
                >
                  {HOUR_LABELS.map((_, i) => {
                    const y = i * HOUR_HEIGHT;
                    if (y > bodyHeight) return null;
                    return (
                      <div
                        key={i}
                        className="absolute left-0 right-0 border-t border-slate-700/30"
                        style={{ top: y }}
                      />
                    );
                  })}

                  {showBreak && BREAK_DAYS.has(day) && BREAK_TOP < bodyHeight && (
                    <div
                      className="absolute left-0 right-0 pointer-events-none"
                      style={{
                        top: BREAK_TOP,
                        height: Math.min(BREAK_HEIGHT, bodyHeight - BREAK_TOP),
                        background: "repeating-linear-gradient(45deg, rgba(148,163,184,0.07) 0px, rgba(148,163,184,0.07) 4px, transparent 4px, transparent 10px)",
                        borderTop: "1px dashed rgba(148,163,184,0.25)",
                        borderBottom: "1px dashed rgba(148,163,184,0.25)",
                        zIndex: 0,
                      }}
                    >
                      <span
                        style={{
                          position: "absolute",
                          bottom: 2,
                          right: 4,
                          fontSize: "0.55rem",
                          color: "rgba(148,163,184,0.5)",
                          fontStyle: "italic",
                          whiteSpace: "nowrap",
                          pointerEvents: "none",
                          userSelect: "none",
                        }}
                      >
                        break 12:30–13:30
                      </span>
                    </div>
                  )}

                  {slots.map((meeting) => {
                    const top = (meeting.clippedStart - START_MINUTES) * PX_PER_MINUTE;
                    const height = (meeting.clippedEnd - meeting.clippedStart) * PX_PER_MINUTE;
                    const laneWidth = 100 / meeting.totalLanes;
                    const left = meeting.lane * laneWidth;
                    const color = colorMap[meeting.courseCode] ?? hueToColor(0);
                    const isShort = height < 45;
                    const blockKey = `${meeting.id}-${meeting.day}-${meeting.lane}`;
                    const isExpanded = expandedCard?.meeting.id === meeting.id &&
                      expandedCard?.meeting.day === meeting.day &&
                      expandedCard?.meeting.lane === meeting.lane;

                    return (
                      <div
                        key={blockKey}
                        onClick={(e) => {
                          const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                          setExpandedCard({ meeting, anchorRect: rect });
                        }}
                        onMouseLeave={() => setExpandedCard(null)}
                        style={{
                          position: "absolute",
                          top,
                          height: Math.max(height, 18),
                          left: `calc(${left}% + 2px)`,
                          width: `calc(${laneWidth}% - 4px)`,
                          backgroundColor: color.bg,
                          borderLeft: `3px solid ${color.border}`,
                          borderRadius: "0.35rem",
                          padding: isShort ? "2px 5px" : "4px 6px",
                          overflow: "hidden",
                          cursor: "pointer",
                          boxShadow: isExpanded
                            ? `0 0 0 2px ${color.border}, 0 4px 20px rgba(0,0,0,0.6)`
                            : "0 2px 8px rgba(0,0,0,0.4)",
                          transition: "box-shadow 0.15s",
                          zIndex: isExpanded ? 50 : 1,
                        }}
                      >
                        <div style={{ color: color.text, fontSize: "0.7rem", fontWeight: 700, lineHeight: 1.2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                          {meeting.courseCode}
                        </div>
                        {!isShort && (
                          <>
                            <div style={{ color: "#94a3b8", fontSize: "0.62rem", lineHeight: 1.2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                              {meeting.sectionCode}
                            </div>
                            <div style={{ color: "#64748b", fontSize: "0.6rem", lineHeight: 1.2 }}>
                              {shortTime(meeting.startTime)}–{shortTime(meeting.endTime)}
                            </div>
                            {meeting.roomCode && height >= 70 && (
                              <div style={{ color: "#475569", fontSize: "0.58rem", lineHeight: 1.2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                                {meeting.roomCode}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {expandedCard && (
        <DetailCard card={expandedCard} colorMap={colorMap} />
      )}
    </div>
  );
}
