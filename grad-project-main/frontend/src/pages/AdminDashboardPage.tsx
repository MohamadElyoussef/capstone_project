import { useEffect, useState } from "react";
import logo from "../assets/logo.jpeg";
import type { ChangeEvent } from "react";
import {
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Eye,
  Loader2,
  FileSpreadsheet,
  FileText,
  FileType2,
  Pencil,
} from "lucide-react";
import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import * as XLSX from "xlsx";
import {
  Document,
  Packer,
  Paragraph,
  Table,
  TableRow,
  TableCell,
  WidthType,
  AlignmentType,
  TextRun,
  BorderStyle,
} from "docx";
import { saveAs } from "file-saver";
import {
  type AdminImportSummary,
  getDataStatus,
  type AuditLogEntry,
  type AvailableSlot,
  type DoctorListItem,
  type DoctorScheduleResponse,
  type GenerateScheduleResponse,
  type RoomOption,
  type ScheduleConflictReportResponse,
  type SectionSuggestion,
  type UnscheduledSection,
  ApiError,
  generateSchedule,
  generateSummerSchedule,
  getActiveScheduleType,
  getAuditLogs,
  getAvailableSlots,
  getDoctorSchedule,
  getDoctors,
  getRooms,
  getScheduleConflicts,
  getSuggestions,
  getUnscheduledSections,
  bulkUpdateSectionInstructor,
  getDoctorCoursesMap,
  importUniversityData,
  manualUpdateSectionSchedule,
} from "../api/client";
import { WeeklyTimetable } from "../components/WeeklyTimetable";
import { clearAuthStorage } from "../lib/auth";

function formatPayload(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function toImportMessage(summary: AdminImportSummary): string {
  return `Rooms imported: ${summary.rooms_imported} | Courses imported: ${summary.courses_imported} | Sections imported: ${summary.sections_imported}`;
}

function formatUaeTime(value: string): string {
  const normalized =
    value.endsWith("Z") || value.includes("+") ? value : `${value}Z`;

  return new Date(normalized).toLocaleString("en-GB", {
    timeZone: "Asia/Dubai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatTimeShort(value: string): string {
  return value.slice(0, 5);
}

function normalizeDoctorName(name: string | null | undefined): string {
  if (!name) return "";
  return name.replace(/^(Dr)\.\s*/i, "Dr. ").trim();
}

function normalizeCourseKey(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function doctorAllowedForCourse(
  doctorName: string,
  courseName: string,
  doctorCoursesMap: Record<string, string[]>,
): boolean {
  const allowed = doctorCoursesMap[doctorName];
  if (!allowed || allowed.length === 0) return true;
  const normCourse = normalizeCourseKey(courseName);
  return allowed.some((a) => {
    const normA = normalizeCourseKey(a);
    return normCourse.includes(normA) || normA.includes(normCourse);
  });
}

function sectionLeadingDigitCount(sectionCode: string): number {
  const m = sectionCode.match(/^(\d+)/);
  return m ? m[1].length : 0;
}

function tbaGroupKey(courseCode: string, sectionCode: string): string {
  return `${courseCode}::${sectionLeadingDigitCount(sectionCode)}`;
}

function getDoctorCourseCount(doctorName: string, assignments: Record<string, string>): number {
  const courses = new Set(
    Object.entries(assignments)
      .filter(([, instr]) => instr === doctorName)
      .map(([code]) => code),
  );
  return courses.size;
}

type DoctorMeeting = {
  section_id: number;
  day: string;
  start_time: string;
  end_time: string;
  course_code: string;
  course_name: string;
  section_code: string;
  section_type: string;
  room_code?: string | null;
  credit_hours?: number | string | null;
  crn?: string | number | null;
};

function getSubjectCode(courseCode: string): string {
  return courseCode.slice(0, 3).toUpperCase();
}

function getCourseNumber(courseCode: string): string {
  const match = courseCode.match(/\d+/);
  return match ? match[0] : "";
}

function getMaxCapacity(sectionType: string): number {
  const normalized = sectionType.trim().toUpperCase();
  if (normalized === "LECTURE") return 50;
  if (normalized === "TUTORIAL") return 40;
  if (normalized === "LAB") return 25;
  return 0;
}

function getMergedValue(sectionCode: string): string {
  const normalized = sectionCode.trim().toUpperCase();
  if (normalized.includes("B")) return "yes";
  if (normalized.includes("M")) return "no";
  if (normalized.includes("F")) return "no";
  return "no";
}

function getCreditHoursForExport(sectionType: string): number {
  const normalized = sectionType.trim().toUpperCase();
  if (normalized === "LECTURE") return 3;
  return 0;
}

function flattenDoctorSchedule(
  schedule: DoctorScheduleResponse | null,
): DoctorMeeting[] {
  if (!schedule) return [];

  const dayOrder = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

  return Object.entries(schedule)
    .sort((a, b) => dayOrder.indexOf(a[0]) - dayOrder.indexOf(b[0]))
    .flatMap(([day, meetings]) =>
      meetings.map((meeting) => ({
        ...(meeting as DoctorMeeting),
        day,
      })),
    );
}

function getGroupedDoctorSchedule(
  schedule: DoctorScheduleResponse | null,
): Array<{ label: string; meetings: DoctorMeeting[] }> {
  if (!schedule) return [];

  const allMeetings = flattenDoctorSchedule(schedule);

  const DAY_PREF = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

  const mkKey = (m: DoctorMeeting) =>
    `${m.course_code}|${m.section_code}|${m.section_type}|${m.start_time}|${m.end_time}`;

  const sectionInfo = new Map<string, { days: Set<string>; rep: DoctorMeeting }>();
  for (const m of allMeetings) {
    const key = mkKey(m);
    const info = sectionInfo.get(key);
    if (!info) {
      sectionInfo.set(key, { days: new Set([m.day]), rep: m });
    } else {
      info.days.add(m.day);
      if (DAY_PREF.indexOf(m.day) < DAY_PREF.indexOf(info.rep.day)) {
        info.rep = m;
      }
    }
  }

  const getGroupLabel = (days: Set<string>): string => {
    const M = days.has("MON");
    const T = days.has("TUE");
    const W = days.has("WED");
    const Th = days.has("THU");
    const F = days.has("FRI");
    const n = days.size;
    if (M && T && W && Th && n === 4) return "MON / TUE / WED / THU";
    if (M && T && W && !Th && n === 3) return "MON / TUE / WED";
    if (M && T && !W && !Th && n === 2) return "MON / TUE";
    if (!M && T && W && !Th && n === 2) return "TUE / WED";
    if (!M && !T && W && Th && n === 2) return "WED / THU";
    if (M && !T && W && !Th && n === 2) return "MON / WED";
    if (!M && T && !W && Th && n === 2) return "TUE / THU";
    if (n === 1) return F ? "FRI" : M ? "MON" : T ? "TUE" : W ? "WED" : Th ? "THU" : days.has("SAT") ? "SAT" : "SUN";
    return DAY_PREF.filter((d) => days.has(d)).join(" / ");
  };

  const LABEL_ORDER = [
    "SAT", "SUN",
    "MON / TUE / WED / THU",
    "MON / TUE / WED",
    "MON / TUE",
    "MON / WED",
    "MON",
    "TUE / WED",
    "TUE / THU",
    "TUE",
    "WED / THU",
    "WED",
    "THU",
    "FRI",
  ];

  const grouped = new Map<string, DoctorMeeting[]>();
  for (const { days, rep } of sectionInfo.values()) {
    const label = getGroupLabel(days);
    const arr = grouped.get(label) ?? [];
    arr.push(rep);
    grouped.set(label, arr);
  }

  return LABEL_ORDER
    .filter((label) => grouped.has(label))
    .map((label) => ({ label, meetings: grouped.get(label)! }));
}

function getDoctorGenderFromSection(sectionCode: string): string {
  const normalized = sectionCode.trim().toUpperCase();
  if (normalized.includes("B")) return "B";
  if (normalized.includes("M")) return "M";
  if (normalized.includes("F")) return "F";
  return "";
}

function getDoctorLectureCreditHours(meetings: DoctorMeeting[]): number {
  const uniqueLectureSections = new Set(
    meetings
      .filter(
        (meeting) => meeting.section_type.trim().toUpperCase() === "LECTURE",
      )
      .map(
        (meeting) =>
          `${meeting.course_code}__${meeting.section_code}__${meeting.section_type}`,
      ),
  );

  return uniqueLectureSections.size * 3;
}

function buildDoctorWordRows(schedule: DoctorScheduleResponse | null) {
  const meetings = flattenDoctorSchedule(schedule);

  const grouped = new Map<
    string,
    {
      courseCode: string;
      courseName: string;
      sectionCode: string;
      gender: string;
      hall: string;
      mon: string;
      tue: string;
      wed: string;
      thu: string;
      fri: string;
      creditHours: number;
    }
  >();

  for (const meeting of meetings) {
    const key = `${meeting.course_code}__${meeting.section_code}__${meeting.section_type}`;

    if (!grouped.has(key)) {
      grouped.set(key, {
        courseCode: meeting.course_code,
        courseName: meeting.course_name,
        sectionCode: meeting.section_code,
        gender: getDoctorGenderFromSection(meeting.section_code),
        hall: meeting.room_code || "",
        mon: "",
        tue: "",
        wed: "",
        thu: "",
        fri: "",
        creditHours:
          meeting.section_type.trim().toUpperCase() === "LECTURE" ? 3 : 0,
      });
    }

    const row = grouped.get(key)!;
    const timeValue = `${formatTimeShort(meeting.start_time)} - ${formatTimeShort(meeting.end_time)}`;

    if (meeting.day === "MON") row.mon = timeValue;
    if (meeting.day === "TUE") row.tue = timeValue;
    if (meeting.day === "WED") row.wed = timeValue;
    if (meeting.day === "THU") row.thu = timeValue;
    if (meeting.day === "FRI") row.fri = timeValue;
  }

  return Array.from(grouped.values()).sort((a, b) => {
    if (a.courseCode !== b.courseCode) {
      return a.courseCode.localeCompare(b.courseCode);
    }
    return a.sectionCode.localeCompare(b.sectionCode);
  });
}

function createCell(
  text: string,
  widthPct?: number,
  bold = false,
  align: "left" | "center" = "center",
) {
  return new TableCell({
    width: widthPct
      ? { size: widthPct, type: WidthType.PERCENTAGE }
      : undefined,
    borders: {
      top: { style: BorderStyle.SINGLE, size: 1, color: "000000" },
      bottom: { style: BorderStyle.SINGLE, size: 1, color: "000000" },
      left: { style: BorderStyle.SINGLE, size: 1, color: "000000" },
      right: { style: BorderStyle.SINGLE, size: 1, color: "000000" },
    },
    children: [
      new Paragraph({
        alignment: align === "left" ? AlignmentType.LEFT : AlignmentType.CENTER,
        children: [
          new TextRun({
            text,
            bold,
          }),
        ],
      }),
    ],
  });
}

const DOCTOR_GRID_DAYS = ["MON", "TUE", "WED", "THU", "FRI"];

function timeToMinutes(value: string): number {
  const [hours, minutes] = value.slice(0, 5).split(":").map(Number);
  return hours * 60 + minutes;
}

function getDoctorGridMeetings(schedule: DoctorScheduleResponse | null) {
  const meetings = flattenDoctorSchedule(schedule);

  return meetings
    .filter((meeting) => DOCTOR_GRID_DAYS.includes(meeting.day))
    .map((meeting) => {
      const startMinutes = timeToMinutes(meeting.start_time);
      const endMinutes = timeToMinutes(meeting.end_time);

      const dayStartMinutes = 8 * 60;
      const top = ((startMinutes - dayStartMinutes) / 60) * 60;
      const height = ((endMinutes - startMinutes) / 60) * 60;

      return {
        ...meeting,
        top,
        height,
      };
    });
}

function getAllScheduledMeetings(
  doctors: DoctorListItem[],
  schedules: Record<string, DoctorScheduleResponse | null>,
) {
  const rows: Array<DoctorMeeting & { instructor: string }> = [];

  for (const doctor of doctors) {
    const schedule = schedules[doctor.instructor];
    if (!schedule) continue;

    const meetings = flattenDoctorSchedule(schedule);
    for (const meeting of meetings) {
      rows.push({
        ...meeting,
        instructor: doctor.instructor,
      });
    }
  }

  return rows;
}

export function AdminDashboardPage() {
  const [summary, setSummary] = useState<GenerateScheduleResponse | null>(null);
  const [classroomLimit, setClassroomLimit] = useState<string>("-");
  const [tutorialClassroomLimit, setTutorialClassroomLimit] = useState<string>("-");
  const [summerClassroomLimit, setSummerClassroomLimit] = useState<string>("-");
  const [summerTutorialClassroomLimit, setSummerTutorialClassroomLimit] = useState<string>("-");
  const [unscheduledSections, setUnscheduledSections] = useState<
    UnscheduledSection[]
  >([]);
  const [suggestions, setSuggestions] = useState<SectionSuggestion[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
  const [doctors, setDoctors] = useState<DoctorListItem[]>([]);
  const [selectedDoctor, setSelectedDoctor] = useState<string | null>(null);
  const [selectedDoctorSchedule, setSelectedDoctorSchedule] =
    useState<DoctorScheduleResponse | null>(null);
  const [conflicts, setConflicts] =
    useState<ScheduleConflictReportResponse | null>(null);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [timetableRefreshKey, setTimetableRefreshKey] = useState(0);
  const [roomsFile, setRoomsFile] = useState<File | null>(null);
  const [coursesFile, setCoursesFile] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<AdminImportSummary | null>(
    null,
  );
  const [importSuccessMessage, setImportSuccessMessage] = useState<
    string | null
  >(null);
  const [importErrorMessage, setImportErrorMessage] = useState<string | null>(
    null,
  );
  const [activeScheduleType, setActiveScheduleType] = useState<"regular" | "summer" | null>(null);
  const [tbaCourseInputs, setTbaCourseInputs] = useState<Record<string, string>>({});
  const [tbaCourseAssigningCode, setTbaCourseAssigningCode] = useState<string | null>(null);
  const [tbaSuggestionsOpen, setTbaSuggestionsOpen] = useState<string | null>(null);
  const [tbaConfirmedAssignments, setTbaConfirmedAssignments] = useState<Record<string, string>>({});
  const [doctorCoursesMap, setDoctorCoursesMap] = useState<Record<string, string[]>>({});
  const [fakeProgress, setFakeProgress] = useState(0);
  const [showProgressBar, setShowProgressBar] = useState(false);
  const [progressLabel, setProgressLabel] = useState("");

  const [showUnscheduled, setShowUnscheduled] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [showConflicts, setShowConflicts] = useState(false);
  const [showAuditLogs, setShowAuditLogs] = useState(false);
  const [showDoctors, setShowDoctors] = useState(false);
  const [dayFilter, setDayFilter] = useState<string>("");
  const [roomFilter, setRoomFilter] = useState<string>("");
  const [doctorNameSort, setDoctorNameSort] = useState<"none" | "asc" | "desc">(
    "none",
  );
  const [doctorCountSort, setDoctorCountSort] = useState<
    "none" | "asc" | "desc"
  >("none");
  const [doctorSchedulesMap, setDoctorSchedulesMap] = useState<
    Record<string, DoctorScheduleResponse | null>
  >({});
  const [rooms, setRooms] = useState<RoomOption[]>([]);
  const [hasData, setHasData] = useState(false);

  const [editingSectionId, setEditingSectionId] = useState<number | null>(null);
  const [editingSectionType, setEditingSectionType] = useState<string>("");
  const [availableSlots, setAvailableSlots] = useState<AvailableSlot[]>([]);
  const [loadingAvailableSlots, setLoadingAvailableSlots] = useState(false);
  const [selectedSlot, setSelectedSlot] = useState<{
    days: string;
    start_time: string;
    end_time: string;
    room_code: string;
  } | null>(null);
  const [editDays, setEditDays] = useState("");
  const [editStartTime, setEditStartTime] = useState("");
  const [editEndTime, setEditEndTime] = useState("");
  const [editRoomId, setEditRoomId] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const [unscheduled, scheduleTypeRes, roomsResponse, dataStatus] =
          await Promise.all([
            getUnscheduledSections(),
            getActiveScheduleType(),
            getRooms(),
            getDataStatus(),
          ]);

        setUnscheduledSections(unscheduled);
        setActiveScheduleType(scheduleTypeRes.schedule_type);
        setRooms(roomsResponse);
        setHasData(dataStatus.sections_count > 0 && dataStatus.rooms_count > 0);
      } catch {
        // Keep page usable even if optional load fails.
      }
    };

    void load();
  }, []);


  useEffect(() => {
    if (!editingSectionId) {
      setAvailableSlots([]);
      return;
    }
    const controller = new AbortController();
    const fetchSlots = async () => {
      setLoadingAvailableSlots(true);
      try {
        const data = await getAvailableSlots(editingSectionId, activeScheduleType ?? "regular");
        if (!controller.signal.aborted) setAvailableSlots(data);
      } catch {
        if (!controller.signal.aborted) setAvailableSlots([]);
      } finally {
        if (!controller.signal.aborted) setLoadingAvailableSlots(false);
      }
    };
    void fetchSlots();
    return () => controller.abort();
  }, [editingSectionId, activeScheduleType]);

  useEffect(() => {
    if (selectedDoctor?.trim().toUpperCase() !== "TBA") return;
    getDoctorCoursesMap()
      .then((map) => setDoctorCoursesMap(map))
      .catch(() => {});
  }, [selectedDoctor]);

  const onLogout = () => {
    clearAuthStorage();
    window.location.href = "/login";
  };

  const onRoomsFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setRoomsFile(file);
  };

  const onCoursesFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setCoursesFile(file);
  };

  const onImport = async () => {
    setImportErrorMessage(null);
    setImportSuccessMessage(null);

    if (!roomsFile) {
      setImportErrorMessage("Please select a Rooms CSV file.");
      return;
    }

    if (!coursesFile) {
      setImportErrorMessage("Please select a Courses CSV file.");
      return;
    }

    setLoadingAction("import");
    try {
      const result = await importUniversityData(roomsFile, coursesFile);
      setImportResult(result);
      setImportSuccessMessage("University data imported successfully.");

      try {
        const [refreshedRooms, dataStatus] = await Promise.all([getRooms(), getDataStatus()]);
        setRooms(refreshedRooms);
        setHasData(dataStatus.sections_count > 0 && dataStatus.rooms_count > 0);
      } catch {
        // Counts will refresh on next page load if this fails.
      }
    } catch (error) {
      console.error("[import error]", error);
      let message = "Import failed.";
      if (error instanceof ApiError) {
        message = error.detail || `Server error (${error.status})`;
      } else if (error instanceof Error) {
        message = error.message || "Import failed.";
      }
      setImportErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const sanitizeScheduleLimitInput = (value: string) => {
    const digitsOnly = value.replace(/\D/g, "");
    if (!digitsOnly) return "";
    const numericValue = parseInt(digitsOnly, 10);
    return numericValue > 0 ? String(numericValue) : "";
  };

  const finalizeScheduleLimitInput = (
    value: string,
    setValue: (nextValue: string) => void,
  ) => {
    const sanitizedValue = sanitizeScheduleLimitInput(value);
    setValue(sanitizedValue === "" ? "-" : sanitizedValue);
  };

  const getScheduleLimitNumber = (value: string) => {
    if (value === "-") return null;
    const numericValue = parseInt(value, 10);
    return Number.isFinite(numericValue) && numericValue > 0
      ? numericValue
      : null;
  };

  const availableLectureRooms = rooms.filter(
    (room) => (room.room_type || "").toUpperCase() !== "LAB",
  ).length;

  const onGenerateSchedule = async () => {
    setErrorMessage(null);

    const classroomLimitNumber = getScheduleLimitNumber(classroomLimit) ?? 5;
    const tutorialLimitNumber = getScheduleLimitNumber(tutorialClassroomLimit) ?? 4;

    setLoadingAction("generate");
    setShowProgressBar(true);
    setFakeProgress(0);
    setProgressLabel("Starting schedule generation...");

    let currentProgress = 0;

    const progressTimer = window.setInterval(() => {
      currentProgress += Math.random() * 12;

      if (currentProgress < 15) {
        setProgressLabel("Loading data...");
      } else if (currentProgress < 35) {
        setProgressLabel("Preparing sections and rooms...");
      } else if (currentProgress < 55) {
        setProgressLabel("Building schedule model...");
      } else if (currentProgress < 75) {
        setProgressLabel("Running scheduling algorithm...");
      } else if (currentProgress < 92) {
        setProgressLabel("Finalizing schedule...");
      } else {
        currentProgress = 92;
        setProgressLabel("Almost done...");
      }

      setFakeProgress(Math.min(Math.floor(currentProgress), 92));
    }, 900);

    try {
      const response = await generateSchedule(
        classroomLimitNumber,
        tutorialLimitNumber,
      );

      window.clearInterval(progressTimer);
      setFakeProgress(100);
      setProgressLabel("Schedule generated successfully.");

      setSummary(response);
      setUnscheduledSections(response.unscheduled_sections);
      setTimetableRefreshKey((value) => value + 1);
      setShowUnscheduled(true);
      setActiveScheduleType("regular");

      window.setTimeout(() => {
        setShowProgressBar(false);
        setFakeProgress(0);
        setProgressLabel("");
      }, 1200);
    } catch (error) {
      window.clearInterval(progressTimer);
      setShowProgressBar(false);
      setFakeProgress(0);
      setProgressLabel("");

      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to generate schedule.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const onLoadUnscheduled = async () => {
    if (showUnscheduled) {
      setShowUnscheduled(false);
      return;
    }

    setErrorMessage(null);
    setLoadingAction("unscheduled");
    try {
      const response = await getUnscheduledSections();
      setUnscheduledSections(response);
      setShowUnscheduled(true);
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to load unscheduled sections.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const onLoadSuggestions = async () => {
    if (showSuggestions) {
      setShowSuggestions(false);
      return;
    }

    setErrorMessage(null);
    setLoadingAction("suggestions");
    try {
      const response = await getSuggestions();
      setSuggestions(response);
      setShowSuggestions(true);
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to load suggestions.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const onLoadAuditLogs = async () => {
    if (showAuditLogs) {
      setShowAuditLogs(false);
      return;
    }

    setErrorMessage(null);
    setLoadingAction("audit-logs");
    try {
      const response = await getAuditLogs();
      setAuditLogs(response);
      setShowAuditLogs(true);
    } catch (error) {
      const message =
        error instanceof ApiError ? error.detail : "Failed to load audit logs.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const onLoadDoctors = async () => {
    if (showDoctors) {
      setShowDoctors(false);
      setSelectedDoctor(null);
      setSelectedDoctorSchedule(null);
      setEditingSectionId(null);
      setEditDays("");
      setEditStartTime("");
      setEditEndTime("");
      setEditRoomId("");
      return;
    }

    setErrorMessage(null);
    setLoadingAction("doctors");
    try {
      const response = await getDoctors();
      setDoctors(response);
      setShowDoctors(true);
    } catch (error) {
      const message =
        error instanceof ApiError ? error.detail : "Failed to load doctors.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const onLoadConflicts = async () => {
    if (showConflicts) {
      setShowConflicts(false);
      return;
    }

    setErrorMessage(null);
    setLoadingAction("conflicts");
    try {
      const response = await getScheduleConflicts();
      setConflicts(response);
      setShowConflicts(true);
    } catch (error) {
      const message =
        error instanceof ApiError ? error.detail : "Failed to load conflicts.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const onViewDoctorSchedule = async (instructorName: string) => {
    setErrorMessage(null);
    setLoadingAction(`doctor-${instructorName}`);
    try {
      const response = await getDoctorSchedule(instructorName);
      setSelectedDoctor(instructorName);
      setSelectedDoctorSchedule(response);
      setDoctorSchedulesMap((prev) => ({
        ...prev,
        [instructorName]: response,
      }));
      setEditingSectionId(null);
      setEditDays("");
      setEditStartTime("");
      setEditEndTime("");
      setEditRoomId("");
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to load doctor schedule.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const onSaveManualEdit = async () => {
    if (!editingSectionId) return;

    setErrorMessage(null);
    setLoadingAction("manual-edit");

    try {
      await manualUpdateSectionSchedule(editingSectionId, {
        days: editDays,
        start_time: editStartTime,
        end_time: editEndTime,
        room_code: editRoomId.trim() || null,
      });

      if (selectedDoctor) {
        const refreshed = await getDoctorSchedule(selectedDoctor);
        setSelectedDoctorSchedule(refreshed);
        setDoctorSchedulesMap((prev) => ({
          ...prev,
          [selectedDoctor]: refreshed,
        }));
      }

      setTimetableRefreshKey((value) => value + 1);

      setEditingSectionId(null);
      setEditingSectionType("");
      setAvailableSlots([]);
      setSelectedSlot(null);
      setEditDays("");
      setEditStartTime("");
      setEditEndTime("");
      setEditRoomId("");
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to update section schedule.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const onAssignCourseInstructor = async (groupKey: string, sectionIds: number[]) => {
    const name = (tbaCourseInputs[groupKey] ?? "").trim();
    if (!name || !name.startsWith("Dr. ") || name.length <= 4) return;
    setTbaCourseAssigningCode(groupKey);
    try {
      await bulkUpdateSectionInstructor(sectionIds, name);
      const [refreshedTba, updatedDoctors] = await Promise.all([
        getDoctorSchedule("TBA"),
        getDoctors(),
      ]);
      setSelectedDoctorSchedule(refreshedTba);
      setDoctorSchedulesMap((prev) => ({ ...prev, TBA: refreshedTba }));
      setDoctors(updatedDoctors);
      setTbaConfirmedAssignments((prev) => ({ ...prev, [groupKey]: name }));
      setTbaCourseInputs((prev) => {
        const next = { ...prev };
        delete next[groupKey];
        return next;
      });
    } catch (error) {
      const message =
        error instanceof ApiError ? error.detail : "Failed to assign instructor.";
      setErrorMessage(message);
    } finally {
      setTbaCourseAssigningCode(null);
    }
  };

  const onExportSummerExcel = async () => {
    setErrorMessage(null);

    try {
      let doctorList = doctors;
      if (doctorList.length === 0) {
        doctorList = await getDoctors();
        setDoctors(doctorList);
      }

      const allRooms = await getRooms();
      const roomCapacityMap = new Map<string, number>();
      for (const room of allRooms) {
        if (room.room_code) {
          roomCapacityMap.set(room.room_code, room.capacity ?? 0);
        }
      }

      const allRows: Record<string, string | number>[] = [];
      const crnMap = new Map<string, number>();
      let nextCrn = 40000;
      const courseToDoctor = new Map<string, string>();

      const sortedDoctors = [...doctorList].sort((a, b) =>
        a.instructor.localeCompare(b.instructor),
      );

      for (const doctor of sortedDoctors) {
        const schedule = await getDoctorSchedule(doctor.instructor);
        const meetings = flattenDoctorSchedule(schedule);

        for (const meeting of meetings) {
          const courseCode = meeting.course_code;
          if (!courseToDoctor.has(courseCode)) {
            courseToDoctor.set(courseCode, doctor.instructor);
          }
          if (courseToDoctor.get(courseCode) !== doctor.instructor) {
            continue;
          }

          const sectionKey = `${meeting.course_code}__${meeting.section_code}__${meeting.section_type}`;

          if (!crnMap.has(sectionKey)) {
            crnMap.set(sectionKey, nextCrn);
            nextCrn += 1;
          }

          const roomCapacity = meeting.room_code
            ? (roomCapacityMap.get(meeting.room_code) ?? 0)
            : 0;

          allRows.push({
            "College code": "En",
            "College desc": "college of engineering and IT",
            "Subject code": getSubjectCode(meeting.course_code),
            "Course number": getCourseNumber(meeting.course_code),
            "Course title": meeting.course_name,
            "Credit hours": getCreditHoursForExport(meeting.section_type),
            "Schedule type": meeting.section_type.toLowerCase(),
            Crn: crnMap.get(sectionKey) ?? "",
            "Section number": meeting.section_code,
            "Max capacity": roomCapacity,
            Day: meeting.day,
            "Meeting time": `${formatTimeShort(meeting.start_time)} - ${formatTimeShort(meeting.end_time)}`,
            Room: meeting.room_code || "-",
            "Instructor name": doctor.instructor,
            Merged: getMergedValue(meeting.section_code),
          });
        }
      }

      if (allRows.length === 0) {
        setErrorMessage("No summer schedule data to export. Generate a summer schedule first.");
        return;
      }

      allRows.sort((a, b) =>
        String(a["Course title"]).localeCompare(String(b["Course title"])),
      );

      const worksheet = XLSX.utils.json_to_sheet(allRows);
      const workbook = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(workbook, worksheet, "Doctor Schedules");
      XLSX.writeFile(workbook, "summer_schedule.xlsx");
    } catch (error) {
      const message =
        error instanceof ApiError ? error.detail : "Failed to export summer Excel.";
      setErrorMessage(message);
    }
  };

  const onGenerateSummerSchedule = async () => {
    setErrorMessage(null);
    setLoadingAction("generate-summer");
    setShowProgressBar(true);
    setFakeProgress(0);
    setProgressLabel("Starting summer schedule generation...");

    let currentProgress = 0;
    const progressTimer = window.setInterval(() => {
      currentProgress += Math.random() * 12;
      if (currentProgress < 15) {
        setProgressLabel("Loading course data...");
      } else if (currentProgress < 35) {
        setProgressLabel("Applying summer rules...");
      } else if (currentProgress < 55) {
        setProgressLabel("Building summer model...");
      } else if (currentProgress < 75) {
        setProgressLabel("Running summer algorithm...");
      } else if (currentProgress < 92) {
        setProgressLabel("Finalizing summer schedule...");
      } else {
        currentProgress = 92;
        setProgressLabel("Almost done...");
      }
      setFakeProgress(Math.min(Math.floor(currentProgress), 92));
    }, 900);

    try {
      const classroomLimitNumber = getScheduleLimitNumber(summerClassroomLimit) ?? 5;
      const tutorialLimitNumber = getScheduleLimitNumber(summerTutorialClassroomLimit) ?? 4;
      const response = await generateSummerSchedule(classroomLimitNumber, tutorialLimitNumber);
      window.clearInterval(progressTimer);
      setFakeProgress(100);
      setProgressLabel("Summer schedule generated successfully.");
      setSummary(response);
      setUnscheduledSections(response.unscheduled_sections);
      setTimetableRefreshKey((value) => value + 1);
      setShowUnscheduled(true);
      setActiveScheduleType("summer");
      window.setTimeout(() => {
        setShowProgressBar(false);
        setFakeProgress(0);
        setProgressLabel("");
      }, 1200);
    } catch (error) {
      window.clearInterval(progressTimer);
      setShowProgressBar(false);
      setFakeProgress(0);
      setProgressLabel("");
      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to generate summer schedule.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const onSelectAvailableRoom = async (slot: AvailableSlot, roomCode: string) => {
    if (!roomCode) {
      if (
        selectedSlot?.days === slot.days &&
        selectedSlot?.start_time === slot.start_time
      ) {
        setSelectedSlot(null);
        setEditDays("");
        setEditStartTime("");
        setEditEndTime("");
        setEditRoomId("");
      }
      return;
    }

    setSelectedSlot({
      days: slot.days,
      start_time: slot.start_time,
      end_time: slot.end_time,
      room_code: roomCode,
    });
    setEditDays(slot.days);
    setEditStartTime(slot.start_time);
    setEditEndTime(slot.end_time);
    setEditRoomId(roomCode);

    if (!editingSectionId) return;
    setErrorMessage(null);
    setLoadingAction("manual-edit");
    try {
      await manualUpdateSectionSchedule(editingSectionId, {
        days: slot.days,
        start_time: slot.start_time,
        end_time: slot.end_time,
        room_code: roomCode.trim() || null,
      });
      if (selectedDoctor) {
        const refreshed = await getDoctorSchedule(selectedDoctor);
        setSelectedDoctorSchedule(refreshed);
        setDoctorSchedulesMap((prev) => ({ ...prev, [selectedDoctor]: refreshed }));
      }
      setTimetableRefreshKey((value) => value + 1);
      setEditingSectionId(null);
      setEditingSectionType("");
      setAvailableSlots([]);
      setSelectedSlot(null);
      setEditDays("");
      setEditStartTime("");
      setEditEndTime("");
      setEditRoomId("");
    } catch (error) {
      const message =
        error instanceof ApiError ? error.detail : "Failed to update section schedule.";
      setErrorMessage(message);
    } finally {
      setLoadingAction(null);
    }
  };

  const renderRoomDropdown = (
    slot: AvailableSlot,
    isThisSlotSelected: boolean,
  ) => (
    <div className="space-y-1.5">
      <label className="block text-[10px] font-medium uppercase tracking-wide text-slate-500">
        Select Room
      </label>
      <select
        value={isThisSlotSelected ? (selectedSlot?.room_code ?? "") : ""}
        onChange={(event) => void onSelectAvailableRoom(slot, event.target.value)}
        className={`w-full rounded-lg border px-3 py-2 text-xs font-medium outline-none transition-all ${isThisSlotSelected ? "border-amber-500/60 bg-amber-950/40 text-amber-100 ring-1 ring-amber-500/30" : "border-slate-700/60 bg-slate-900/70 text-slate-300 hover:border-slate-600 focus:border-emerald-600/70 focus:ring-1 focus:ring-emerald-700/40"}`}
      >
        <option value="">Select Room</option>
        {slot.available_rooms.map((room) => (
          <option key={room.id} value={room.room_code}>
            {room.room_code}
          </option>
        ))}
      </select>
    </div>
  );


  const onExportDoctorPdf = () => {
    if (!selectedDoctor || !selectedDoctorSchedule) return;

    const meetings = flattenDoctorSchedule(selectedDoctorSchedule);

    if (meetings.length === 0) {
      setErrorMessage("No doctor schedule available to export.");
      return;
    }

    const pdf = new jsPDF();
    pdf.setFontSize(16);
    pdf.text(`Doctor Schedule: ${selectedDoctor}`, 14, 15);

    autoTable(pdf, {
      startY: 24,
      head: [
        [
          "Day",
          "Course Code",
          "Course Name",
          "Section",
          "Type",
          "Time",
          "Room",
        ],
      ],
      body: meetings.map((meeting) => [
        meeting.day,
        meeting.course_code,
        meeting.course_name,
        meeting.section_code,
        meeting.section_type,
        `${formatTimeShort(meeting.start_time)} - ${formatTimeShort(meeting.end_time)}`,
        meeting.room_code || "-",
      ]),
      styles: { fontSize: 9 },
      headStyles: { fillColor: [79, 70, 229] },
    });

    pdf.save(`doctor_schedule_${selectedDoctor.replace(/\s+/g, "_")}.pdf`);
  };

  const onExportDoctorExcel = async () => {
    setErrorMessage(null);

    try {
      let doctorList = doctors;

      if (doctorList.length === 0) {
        doctorList = await getDoctors();
        setDoctors(doctorList);
      }

      const allRows: Record<string, string | number>[] = [];
      const crnMap = new Map<string, number>();
      let nextCrn = 10000;

      const sortedDoctors = [...doctorList].sort((a, b) =>
        a.instructor.localeCompare(b.instructor),
      );

      for (const doctor of sortedDoctors) {
        const schedule = await getDoctorSchedule(doctor.instructor);
        const meetings = flattenDoctorSchedule(schedule);

        for (const meeting of meetings) {
          const sectionKey = `${meeting.course_code}__${meeting.section_code}__${meeting.section_type}`;

          if (!crnMap.has(sectionKey)) {
            crnMap.set(sectionKey, nextCrn);
            nextCrn += 1;
          }

          allRows.push({
            "College code": "En",
            "College desc": "college of engineering and IT",
            "Subject code": getSubjectCode(meeting.course_code),
            "Course number": getCourseNumber(meeting.course_code),
            "Course title": meeting.course_name,
            "Credit hours": getCreditHoursForExport(meeting.section_type),
            "Schedule type": meeting.section_type.toLowerCase(),
            Crn: crnMap.get(sectionKey) ?? "",
            "Section number": meeting.section_code,
            "Max capacity": getMaxCapacity(meeting.section_type),
            Day: meeting.day,
            "Meeting time": `${formatTimeShort(meeting.start_time)} - ${formatTimeShort(meeting.end_time)}`,
            Room: meeting.room_code || "-",
            "Instructor name": doctor.instructor,
            Merged: getMergedValue(meeting.section_code),
          });
        }
      }

      if (allRows.length === 0) {
        setErrorMessage("No doctor schedules available to export.");
        return;
      }

      allRows.sort((a, b) =>
        String(a["Course title"]).localeCompare(String(b["Course title"])),
      );

      const worksheet = XLSX.utils.json_to_sheet(allRows);
      const workbook = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(workbook, worksheet, "Doctor Schedules");

      XLSX.writeFile(workbook, "all_doctor_schedules.xlsx");
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to export Excel file.";
      setErrorMessage(message);
    }
  };

  const ensureAllDoctorSchedulesLoaded = async () => {
    let doctorList = doctors;

    if (doctorList.length === 0) {
      doctorList = await getDoctors();
      setDoctors(doctorList);
    }

    const missingDoctors = doctorList.filter(
      (doctor) => !doctorSchedulesMap[doctor.instructor],
    );

    if (missingDoctors.length === 0) {
      return {
        doctorList,
        schedulesMap: doctorSchedulesMap,
      };
    }

    setLoadingAction("load-all-doctor-schedules");

    try {
      const loadedEntries = await Promise.all(
        missingDoctors.map(async (doctor) => {
          const schedule = await getDoctorSchedule(doctor.instructor);
          return [doctor.instructor, schedule] as const;
        }),
      );

      const newSchedules = Object.fromEntries(loadedEntries);

      const mergedMap = {
        ...doctorSchedulesMap,
        ...newSchedules,
      };

      setDoctorSchedulesMap(mergedMap);

      return {
        doctorList,
        schedulesMap: mergedMap,
      };
    } finally {
      setLoadingAction(null);
    }
  };

  const onDayFilterChange = async (value: string) => {
    setErrorMessage(null);
    setDayFilter(value);

    if (!value && !roomFilter.trim()) {
      return;
    }

    try {
      await ensureAllDoctorSchedulesLoaded();
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to load schedules for filtering.";
      setErrorMessage(message);
    }
  };

  const onRoomFilterChange = async (value: string) => {
    setErrorMessage(null);
    setRoomFilter(value);

    if (!value.trim() && !dayFilter) {
      return;
    }

    try {
      await ensureAllDoctorSchedulesLoaded();
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to load schedules for filtering.";
      setErrorMessage(message);
    }
  };

  const onExportDoctorWord = async (instructorName: string) => {
    setErrorMessage(null);

    try {
      let schedule = doctorSchedulesMap[instructorName] ?? null;

      if (!schedule) {
        schedule = await getDoctorSchedule(instructorName);
        setDoctorSchedulesMap((prev) => ({
          ...prev,
          [instructorName]: schedule,
        }));
      }

      const meetings = flattenDoctorSchedule(schedule);

      if (meetings.length === 0) {
        setErrorMessage("No doctor schedule available to export.");
        return;
      }

      const rows = buildDoctorWordRows(schedule);
      const totalCreditHours = getDoctorLectureCreditHours(meetings);

      const scheduleTable = new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        rows: [
          new TableRow({
            children: [
              createCell("Course Code", 12, true),
              createCell("Course name", 26, true),
              createCell("Sec", 7, true),
              createCell("Gen", 6, true),
              createCell("Mon", 10, true),
              createCell("Tue", 10, true),
              createCell("Wed", 10, true),
              createCell("Thu", 10, true),
              createCell("Fri", 10, true),
              createCell("Hall", 11, true),
              createCell("Credit Hours", 8, true),
            ],
          }),
          ...rows.map(
            (row) =>
              new TableRow({
                children: [
                  createCell(row.courseCode),
                  createCell(row.courseName, undefined, false, "left"),
                  createCell(row.sectionCode),
                  createCell(row.gender),
                  createCell(row.mon),
                  createCell(row.tue),
                  createCell(row.wed),
                  createCell(row.thu),
                  createCell(row.fri),
                  createCell(row.hall),
                  createCell(String(row.creditHours)),
                ],
              }),
          ),
        ],
      });

      const totalCreditsTable = new Table({
        width: { size: 45, type: WidthType.PERCENTAGE },
        alignment: AlignmentType.CENTER,
        rows: [
          new TableRow({
            children: [
              createCell("Total Credit Hours", 70, true),
              createCell(String(totalCreditHours), 30, true),
            ],
          }),
        ],
      });

      const officeHoursTable = new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        rows: [
          new TableRow({
            children: [
              createCell("Office Hours", 18, true),
              createCell("", 22, true),
              createCell("Mon", 12, true),
              createCell("Tue", 12, true),
              createCell("Wed", 12, true),
              createCell("Thu", 12, true),
              createCell("Fri", 12, true),
            ],
          }),
          new TableRow({
            children: [
              createCell("", 18),
              createCell("Male", 22, true),
              createCell("", 12),
              createCell("", 12),
              createCell("", 12),
              createCell("", 12),
              createCell("", 12),
            ],
          }),
          new TableRow({
            children: [
              createCell("", 18),
              createCell("Female", 22, true),
              createCell("", 12),
              createCell("", 12),
              createCell("", 12),
              createCell("", 12),
              createCell("", 12),
            ],
          }),
        ],
      });

      const doc = new Document({
        sections: [
          {
            children: [
              new Paragraph({
                alignment: AlignmentType.CENTER,
                children: [
                  new TextRun({
                    text: `Doctor Schedule - ${instructorName}`,
                    bold: true,
                    size: 28,
                  }),
                ],
              }),
              new Paragraph({ text: "" }),
              scheduleTable,
              new Paragraph({ text: "" }),
              totalCreditsTable,
              new Paragraph({ text: "" }),
              officeHoursTable,
            ],
          },
        ],
      });

      const blob = await Packer.toBlob(doc);
      saveAs(
        blob,
        `doctor_schedule_${instructorName.replace(/\s+/g, "_")}.docx`,
      );
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.detail
          : "Failed to export Word file.";
      setErrorMessage(message);
    }
  };

  const onSortDoctorNameCycle = () => {
    setDoctorCountSort("none");
    if (doctorNameSort === "none") {
      setDoctorNameSort("asc");
    } else if (doctorNameSort === "asc") {
      setDoctorNameSort("desc");
    } else {
      setDoctorNameSort("none");
    }
  };

  const onSortDoctorCountCycle = () => {
    setDoctorNameSort("none");
    if (doctorCountSort === "none") {
      setDoctorCountSort("asc");
    } else if (doctorCountSort === "asc") {
      setDoctorCountSort("desc");
    } else {
      setDoctorCountSort("none");
    }
  };

  const normalizedRoomFilter = roomFilter.trim().toLowerCase();

  const filteredDoctors = [...doctors]
    .filter((doctor) => {
      if (!dayFilter && !normalizedRoomFilter) {
        return true;
      }

      const schedule = doctorSchedulesMap[doctor.instructor];
      if (!schedule) {
        return true;
      }

      const meetings = flattenDoctorSchedule(schedule);

      return meetings.some((meeting) => {
        const matchesDay = !dayFilter || meeting.day === dayFilter;
        const matchesRoom =
          !normalizedRoomFilter ||
          (meeting.room_code ?? "")
            .toLowerCase()
            .includes(normalizedRoomFilter);

        return matchesDay && matchesRoom;
      });
    })
    .sort((a, b) => {
      if (doctorNameSort !== "none") {
        const nameA = normalizeDoctorName(a.instructor).toLowerCase();
        const nameB = normalizeDoctorName(b.instructor).toLowerCase();
        const cmp = nameA.localeCompare(nameB, undefined, {
          sensitivity: "base",
          numeric: true,
        });
        return doctorNameSort === "asc" ? cmp : -cmp;
      }

      if (doctorCountSort !== "none") {
        return doctorCountSort === "asc"
          ? a.sections_count - b.sections_count
          : b.sections_count - a.sections_count;
      }

      return 0;
    });

  const inputClass =
    "bg-slate-800 border border-slate-700 text-slate-200 placeholder:text-slate-500 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-indigo-500 transition-colors";
  const selectClass =
    "bg-slate-800 border border-slate-700 text-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-indigo-500 transition-colors disabled:opacity-50";
  const btnPrimary =
    "px-3 py-1.5 bg-gradient-to-r from-indigo-600 to-cyan-600 hover:from-indigo-500 hover:to-cyan-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium transition-all";
  const btnSecondary =
    "px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed text-slate-200 rounded-lg border border-slate-600 text-sm font-medium transition-colors";
  const panelClass =
    "bg-slate-800/60 backdrop-blur-md border border-slate-700/50 rounded-xl p-4 space-y-3";
  const tableHead =
    "bg-slate-700/60 text-slate-300 text-xs uppercase tracking-wider";
  const tdClass = "px-3 py-2 text-xs";

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="bg-slate-900/80 backdrop-blur-md border-b border-slate-700/50 px-5 py-3.5 flex justify-between items-center gap-4 sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <img
            src={logo}
            alt="UniSchedule logo"
            className="w-9 h-9 rounded-xl object-cover bg-white p-0.5 flex-shrink-0"
          />
          <div>
            <p className="text-xs text-slate-500">
              Admin &gt; Scheduling &gt; Schedule
            </p>
            <h1 className="text-sm font-bold text-slate-100 leading-tight">
              Admin Scheduling Dashboard
            </h1>
          </div>
          {activeScheduleType !== null ? (
            <div
              className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border ${
                activeScheduleType === "summer"
                  ? "bg-amber-500/15 border-amber-400/40 text-amber-300"
                  : "bg-indigo-500/15 border-indigo-400/40 text-indigo-300"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  activeScheduleType === "summer"
                    ? "bg-amber-400"
                    : "bg-indigo-400"
                }`}
              />
              {activeScheduleType === "summer" ? "Summer Schedule Active" : "Regular Schedule Active"}
            </div>
          ) : null}
        </div>
        <button type="button" onClick={onLogout} className={btnSecondary}>
          Logout
        </button>
      </header>

      <div className="max-w-screen-2xl mx-auto p-4 md:p-5 space-y-4">
        {errorMessage ? (
          <div className="bg-red-900/40 border border-red-500/40 text-red-300 rounded-xl px-4 py-3 text-sm">
            {errorMessage}
          </div>
        ) : null}

        {showProgressBar ? (
          <div className={panelClass}>
            <div className="flex justify-between items-center">
              <h2 className="text-sm font-semibold text-slate-300">
                Schedule Generation
              </h2>
              <span className="text-sm font-mono text-indigo-400">
                {fakeProgress}%
              </span>
            </div>
            <div className="h-2.5 bg-slate-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-indigo-600 to-cyan-500 rounded-full transition-all duration-500 ease-out"
                style={{ width: `${fakeProgress}%` }}
              />
            </div>
            <p className="text-xs text-slate-400">{progressLabel}</p>
          </div>
        ) : null}

        <div className={panelClass}>
          <div className="flex justify-between items-center flex-wrap gap-3">
            <h2 className="text-sm font-semibold text-slate-300">
              Summer Schedule
            </h2>
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium text-slate-400 tracking-wide uppercase">
                Class Rooms for Tutorial &amp; Lectures
              </label>
              <div className="flex flex-wrap gap-4">
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] text-slate-500 uppercase tracking-wide">Lectures</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={summerClassroomLimit}
                    onFocus={() => { if (summerClassroomLimit === "-") setSummerClassroomLimit(""); }}
                    onChange={(e) => setSummerClassroomLimit(sanitizeScheduleLimitInput(e.target.value))}
                    onBlur={(e) => finalizeScheduleLimitInput(e.target.value, setSummerClassroomLimit)}
                    disabled={loadingAction !== null}
                    className="w-24 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-100 text-center focus:border-amber-500 focus:outline-none focus:ring-1 focus:ring-amber-500 disabled:opacity-50"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] text-slate-500 uppercase tracking-wide">Tutorials</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={summerTutorialClassroomLimit}
                    onFocus={() => { if (summerTutorialClassroomLimit === "-") setSummerTutorialClassroomLimit(""); }}
                    onChange={(e) => setSummerTutorialClassroomLimit(sanitizeScheduleLimitInput(e.target.value))}
                    onBlur={(e) => finalizeScheduleLimitInput(e.target.value, setSummerTutorialClassroomLimit)}
                    disabled={loadingAction !== null}
                    className="w-24 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-100 text-center focus:border-amber-500 focus:outline-none focus:ring-1 focus:ring-amber-500 disabled:opacity-50"
                  />
                </div>
              </div>
            </div>
            <div className="flex flex-col gap-1 text-xs text-slate-400">
              <span>
                Available tutorial &amp; lecture classrooms:{" "}
                <span className="text-slate-100 font-medium">{availableLectureRooms}</span>
              </span>
              <span>
                Lab classrooms:{" "}
                <span className="text-slate-100 font-medium">
                  {rooms.filter((r) => (r.room_type || "").toUpperCase() === "LAB").length}
                </span>
              </span>
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <button
              type="button"
              onClick={() => void onGenerateSummerSchedule()}
              disabled={loadingAction !== null || !hasData}
              className="px-3 py-1.5 bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium transition-all"
            >
              {loadingAction === "generate-summer"
                ? "Generating..."
                : "Generate Summer Schedule"}
            </button>
            {!hasData ? (
              <p className="text-xs text-amber-500/80">Import Rooms &amp; Courses CSV first.</p>
            ) : null}
          </div>
        </div>

        <div className={panelClass}>
          <div className="flex justify-between items-center flex-wrap gap-2">
            <h2 className="text-sm font-semibold text-slate-300">
              Import University Data
            </h2>
            {loadingAction === "import" ? (
              <span className="text-xs text-slate-400">Importing...</span>
            ) : null}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 items-end">
            <label className="block space-y-1">
              <span className="text-xs text-slate-400">Rooms CSV</span>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={onRoomsFileChange}
                className="block w-full text-xs text-slate-400 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:bg-slate-700 file:text-slate-200 hover:file:bg-slate-600 bg-slate-800 border border-slate-700 rounded-lg p-1.5 cursor-pointer"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-slate-400">Courses CSV</span>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={onCoursesFileChange}
                className="block w-full text-xs text-slate-400 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:bg-slate-700 file:text-slate-200 hover:file:bg-slate-600 bg-slate-800 border border-slate-700 rounded-lg p-1.5 cursor-pointer"
              />
            </label>
            <div className="flex items-end">
              <button
                type="button"
                onClick={() => void onImport()}
                disabled={loadingAction !== null}
                className={btnPrimary}
              >
                {loadingAction === "import" ? "Importing..." : "Import Data"}
              </button>
            </div>
          </div>
          {importSuccessMessage ? (
            <div className="bg-indigo-900/40 border border-indigo-500/40 text-indigo-300 rounded-lg px-3 py-2 text-sm">
              {importSuccessMessage}
            </div>
          ) : null}
          {importErrorMessage ? (
            <div className="bg-red-900/40 border border-red-500/40 text-red-300 rounded-lg px-3 py-2 text-sm">
              {importErrorMessage}
            </div>
          ) : null}
          {importResult ? (
            <div className="space-y-2">
              <p className="text-xs text-slate-400">
                {toImportMessage(importResult)}
              </p>
              <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-400 bg-slate-800/60 border border-slate-700 rounded-lg px-3 py-2">
                <span>
                  Available tutorial &amp; lecture classrooms:{" "}
                  <span className="text-slate-100 font-semibold">
                    {importResult.lecture_rooms_count}
                  </span>
                </span>
                <span>
                  Lab classrooms:{" "}
                  <span className="text-slate-100 font-semibold">
                    {importResult.lab_rooms_count}
                  </span>
                </span>
              </div>
            </div>
          ) : null}
        </div>

        <div className={panelClass}>
          <div className="flex flex-wrap items-end gap-4 mb-4">
            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium text-slate-400 tracking-wide uppercase">
                Class Rooms for Tutorial &amp; Lectures
              </label>
              <div className="flex flex-wrap gap-4">
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] text-slate-500 uppercase tracking-wide">
                    Lectures
                  </span>
                  <input
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={classroomLimit}
                    onFocus={() => {
                      if (classroomLimit === "-") setClassroomLimit("");
                    }}
                    onChange={(e) =>
                      setClassroomLimit(sanitizeScheduleLimitInput(e.target.value))
                    }
                    onBlur={(e) =>
                      finalizeScheduleLimitInput(e.target.value, setClassroomLimit)
                    }
                    disabled={loadingAction !== null}
                    className="w-24 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-100 text-center focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] text-slate-500 uppercase tracking-wide">
                    Tutorials
                  </span>
                  <input
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={tutorialClassroomLimit}
                    onFocus={() => {
                      if (tutorialClassroomLimit === "-") setTutorialClassroomLimit("");
                    }}
                    onChange={(e) =>
                      setTutorialClassroomLimit(sanitizeScheduleLimitInput(e.target.value))
                    }
                    onBlur={(e) =>
                      finalizeScheduleLimitInput(e.target.value, setTutorialClassroomLimit)
                    }
                    disabled={loadingAction !== null}
                    className="w-24 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-100 text-center focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
                  />
                </div>
              </div>
            </div>
            <div className="flex flex-col gap-1 text-xs text-slate-400">
              <span>
                Available tutorial &amp; lecture classrooms:{" "}
                <span className="text-slate-100 font-medium">
                  {availableLectureRooms}
                </span>
              </span>
              <span>
                Lab classrooms:{" "}
                <span className="text-slate-100 font-medium">
                  {rooms.filter(
                    (room) => (room.room_type || "").toUpperCase() === "LAB",
                  ).length}
                </span>
              </span>
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex flex-col gap-1">
              <button
                type="button"
                onClick={() => void onGenerateSchedule()}
                disabled={loadingAction !== null || !hasData}
                className={btnPrimary}
              >
                {loadingAction === "generate"
                  ? "Generating..."
                  : "Generate Schedule"}
              </button>
              {!hasData ? (
                <p className="text-xs text-amber-500/80">Import Rooms &amp; Courses CSV first.</p>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-2">
              {[
                {
                  key: "unscheduled",
                  show: showUnscheduled,
                  onLoad: onLoadUnscheduled,
                  label: "Unscheduled",
                },
                {
                  key: "suggestions",
                  show: showSuggestions,
                  onLoad: onLoadSuggestions,
                  label: "Suggestions",
                },
                {
                  key: "conflicts",
                  show: showConflicts,
                  onLoad: onLoadConflicts,
                  label: "Conflicts",
                },
                {
                  key: "audit-logs",
                  show: showAuditLogs,
                  onLoad: onLoadAuditLogs,
                  label: "Audit Log",
                },
                {
                  key: "doctors",
                  show: showDoctors,
                  onLoad: onLoadDoctors,
                  label: "Doctors",
                },
              ].map(({ key, show, onLoad, label }) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => void onLoad()}
                  disabled={loadingAction !== null}
                  className={btnSecondary}
                >
                  {loadingAction === key
                    ? "Loading..."
                    : show
                      ? `Hide ${label}`
                      : `View ${label}`}
                </button>
              ))}
            </div>
          </div>
          {summary ? (
            <div className="flex flex-wrap gap-5 text-sm">
              <span className="text-slate-400">
                Total:{" "}
                <strong className="text-indigo-300">
                  {summary.total_sections}
                </strong>
              </span>
              <span className="text-slate-400">
                Scheduled:{" "}
                <strong className="text-emerald-300">
                  {summary.scheduled_sections}
                </strong>
              </span>
              <span className="text-slate-400">
                Conflicts:{" "}
                <strong className="text-rose-300">
                  {summary.conflicts_found}
                </strong>
              </span>
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              Generate a schedule to view summary data.
            </p>
          )}
        </div>

        {showUnscheduled ? (
          <div className={panelClass}>
            <div className="flex justify-between items-center flex-wrap gap-2">
              <h2 className="text-sm font-semibold text-slate-300">
                Unscheduled Sections
              </h2>
              <span className="text-xs text-slate-500">
                {unscheduledSections.length} sections
              </span>
            </div>
            <div className="overflow-auto rounded-lg border border-slate-700">
              <table className="w-full min-w-[800px]">
                <thead>
                  <tr className={tableHead}>
                    <th className="px-3 py-2.5 text-left">Section Code</th>
                    <th className="px-3 py-2.5 text-left">Type</th>
                    <th className="px-3 py-2.5 text-left">Course Code</th>
                    <th className="px-3 py-2.5 text-left">Course Name</th>
                    <th className="px-3 py-2.5 text-left">Instructor</th>
                    <th className="px-3 py-2.5 text-left">Gender</th>
                    <th className="px-3 py-2.5 text-left">Reason</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/50">
                  {unscheduledSections.map((item) => (
                    <tr
                      key={item.section_id}
                      className="hover:bg-slate-700/20 transition-colors"
                    >
                      <td className={`${tdClass} text-slate-200 font-mono`}>
                        {item.section_code}
                      </td>
                      <td className={`${tdClass} text-slate-300`}>
                        {item.section_type}
                      </td>
                      <td className={`${tdClass} text-indigo-300 font-medium`}>
                        {item.course_code}
                      </td>
                      <td className={`${tdClass} text-slate-300`}>
                        {item.course_name}
                      </td>
                      <td className={`${tdClass} text-slate-300`}>
                        {normalizeDoctorName(item.instructor) || "-"}
                      </td>
                      <td className={`${tdClass} text-slate-400`}>
                        {item.gender_allowed}
                      </td>
                      <td className={`${tdClass} text-amber-300`}>
                        {item.reason}
                      </td>
                    </tr>
                  ))}
                  {unscheduledSections.length === 0 ? (
                    <tr>
                      <td
                        colSpan={7}
                        className="px-3 py-6 text-center text-slate-500 text-sm"
                      >
                        No unscheduled sections found.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {showSuggestions ? (
          <div className={panelClass}>
            <div className="flex justify-between items-center flex-wrap gap-2">
              <h2 className="text-sm font-semibold text-slate-300">
                Automatic Suggestions
              </h2>
              <span className="text-xs text-slate-500">
                {suggestions.length} sections
              </span>
            </div>
            <div className="space-y-2">
              {suggestions.length === 0 ? (
                <p className="text-sm text-slate-500">
                  No suggestions loaded yet.
                </p>
              ) : (
                suggestions.map((item) => (
                  <details
                    key={item.section_id}
                    className="bg-slate-900/50 border border-slate-700 rounded-lg group"
                  >
                    <summary className="px-3 py-2.5 cursor-pointer text-sm text-slate-300 font-medium list-none flex justify-between items-center">
                      <span>
                        {item.section_code} — {item.reason}
                      </span>
                      <span className="text-slate-500 text-xs">▼</span>
                    </summary>
                    <div className="border-t border-slate-700 p-3 space-y-2">
                      {item.suggestions.length === 0 ? (
                        <p className="text-xs text-slate-500">
                          No suggestions available.
                        </p>
                      ) : (
                        item.suggestions.map((s, index) => (
                          <div
                            key={`${item.section_id}-${index}`}
                            className="bg-slate-800 rounded-lg p-2.5 space-y-1 border border-slate-700"
                          >
                            <h4 className="text-xs font-semibold text-indigo-300">
                              {s.type}
                            </h4>
                            <p className="text-xs text-slate-300">
                              {s.message}
                            </p>
                            <pre className="text-xs text-slate-400 whitespace-pre-wrap break-words">
                              {formatPayload(s.payload)}
                            </pre>
                          </div>
                        ))
                      )}
                    </div>
                  </details>
                ))
              )}
            </div>
          </div>
        ) : null}

        {showConflicts ? (
          <div className={panelClass}>
            <div className="flex justify-between items-center flex-wrap gap-2">
              <h2 className="text-sm font-semibold text-slate-300">
                Schedule Conflicts
              </h2>
              <span className="text-xs text-slate-500">
                {(conflicts?.room_conflicts.length ?? 0) +
                  (conflicts?.instructor_conflicts.length ?? 0)}{" "}
                conflicts
              </span>
            </div>

            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider pt-1">
              Room Conflicts
            </h3>
            <div className="overflow-auto rounded-lg border border-slate-700">
              <table className="w-full min-w-[600px]">
                <thead>
                  <tr className={tableHead}>
                    <th className="px-3 py-2.5 text-left">Day</th>
                    <th className="px-3 py-2.5 text-left">Time</th>
                    <th className="px-3 py-2.5 text-left">First Section</th>
                    <th className="px-3 py-2.5 text-left">Second Section</th>
                    <th className="px-3 py-2.5 text-left">Room</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/50">
                  {conflicts?.room_conflicts.map((item, index) => (
                    <tr key={`room-${index}`} className="hover:bg-slate-700/20">
                      <td className={`${tdClass} text-slate-300`}>
                        {item.day}
                      </td>
                      <td className={`${tdClass} text-slate-300`}>
                        {item.overlap_start} - {item.overlap_end}
                      </td>
                      <td className={`${tdClass} text-slate-200 font-mono`}>
                        {item.first_section_code}
                      </td>
                      <td className={`${tdClass} text-slate-200 font-mono`}>
                        {item.second_section_code}
                      </td>
                      <td className={`${tdClass} text-cyan-300`}>
                        {item.room_code || "-"}
                      </td>
                    </tr>
                  ))}
                  {!conflicts || conflicts.room_conflicts.length === 0 ? (
                    <tr>
                      <td
                        colSpan={5}
                        className="px-3 py-5 text-center text-slate-500 text-xs"
                      >
                        No room conflicts found.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider pt-2">
              Instructor Conflicts
            </h3>
            <div className="overflow-auto rounded-lg border border-slate-700">
              <table className="w-full min-w-[600px]">
                <thead>
                  <tr className={tableHead}>
                    <th className="px-3 py-2.5 text-left">Day</th>
                    <th className="px-3 py-2.5 text-left">Time</th>
                    <th className="px-3 py-2.5 text-left">First Section</th>
                    <th className="px-3 py-2.5 text-left">Second Section</th>
                    <th className="px-3 py-2.5 text-left">Instructor</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/50">
                  {conflicts?.instructor_conflicts.map((item, index) => (
                    <tr
                      key={`instr-${index}`}
                      className="hover:bg-slate-700/20"
                    >
                      <td className={`${tdClass} text-slate-300`}>
                        {item.day}
                      </td>
                      <td className={`${tdClass} text-slate-300`}>
                        {item.overlap_start} - {item.overlap_end}
                      </td>
                      <td className={`${tdClass} text-slate-200 font-mono`}>
                        {item.first_section_code}
                      </td>
                      <td className={`${tdClass} text-slate-200 font-mono`}>
                        {item.second_section_code}
                      </td>
                      <td className={`${tdClass} text-slate-300`}>
                        {normalizeDoctorName(item.instructor) || "-"}
                      </td>
                    </tr>
                  ))}
                  {!conflicts || conflicts.instructor_conflicts.length === 0 ? (
                    <tr>
                      <td
                        colSpan={5}
                        className="px-3 py-5 text-center text-slate-500 text-xs"
                      >
                        No instructor conflicts found.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {showAuditLogs ? (
          <div className={panelClass}>
            <div className="flex justify-between items-center flex-wrap gap-2">
              <h2 className="text-sm font-semibold text-slate-300">
                Audit Log
              </h2>
              <span className="text-xs text-slate-500">
                {auditLogs.length} entries
              </span>
            </div>
            <div className="overflow-auto rounded-lg border border-slate-700">
              <table className="w-full min-w-[700px]">
                <thead>
                  <tr className={tableHead}>
                    <th className="px-3 py-2.5 text-left">Time (UAE)</th>
                    <th className="px-3 py-2.5 text-left">User</th>
                    <th className="px-3 py-2.5 text-left">Action</th>
                    <th className="px-3 py-2.5 text-left">Entity</th>
                    <th className="px-3 py-2.5 text-left">Entity ID</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/50">
                  {auditLogs.map((log) => (
                    <tr key={log.id} className="hover:bg-slate-700/20">
                      <td className={`${tdClass} text-slate-400 font-mono`}>
                        {formatUaeTime(log.timestamp)}
                      </td>
                      <td className={`${tdClass} text-slate-300`}>
                        {log.actor_username || "-"}
                      </td>
                      <td className={`${tdClass} text-indigo-300 font-medium`}>
                        {log.action}
                      </td>
                      <td className={`${tdClass} text-slate-300`}>
                        {log.entity_type}
                      </td>
                      <td className={`${tdClass} text-slate-400 font-mono`}>
                        {log.entity_id || "-"}
                      </td>
                    </tr>
                  ))}
                  {auditLogs.length === 0 ? (
                    <tr>
                      <td
                        colSpan={5}
                        className="px-3 py-5 text-center text-slate-500 text-xs"
                      >
                        No audit logs loaded yet.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {showDoctors ? (
          <>
            <div className={panelClass}>
              <div className="flex justify-between items-center flex-wrap gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-300">
                    Doctor Schedules
                  </h2>
                  <span className="text-xs text-slate-500">
                    {filteredDoctors.length} doctors
                  </span>
                </div>
                <div className="flex gap-2 flex-wrap">
                  <button
                    type="button"
                    onClick={() => void (activeScheduleType === "summer" ? onExportSummerExcel() : onExportDoctorExcel())}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-emerald-950/70 hover:bg-emerald-900/70 border border-emerald-600/40 hover:border-emerald-400/60 text-emerald-400 hover:text-emerald-300 shadow-sm hover:shadow-emerald-950/50"
                  >
                    <FileSpreadsheet size={13} />
                    Export as Excel
                  </button>
                </div>
              </div>

              <div className="flex flex-wrap gap-3 items-end">
                <label className="block space-y-1">
                  <span className="text-xs text-slate-400">Day</span>
                  <select
                    value={dayFilter}
                    onChange={(event) =>
                      void onDayFilterChange(event.target.value)
                    }
                    disabled={loadingAction !== null}
                    className={selectClass}
                  >
                    <option value="">All Days</option>
                    <option value="SUN">SUN</option>
                    <option value="MON">MON</option>
                    <option value="TUE">TUE</option>
                    <option value="WED">WED</option>
                    <option value="THU">THU</option>
                    <option value="FRI">FRI</option>
                    <option value="SAT">SAT</option>
                  </select>
                </label>

                <label className="block space-y-1">
                  <span className="text-xs text-slate-400">Room</span>
                  <input
                    type="text"
                    value={roomFilter}
                    onChange={(event) =>
                      void onRoomFilterChange(event.target.value)
                    }
                    placeholder="Filter by room"
                    disabled={loadingAction !== null}
                    className={inputClass}
                  />
                </label>

                {dayFilter || roomFilter.trim() ? (
                  <button
                    type="button"
                    onClick={() => {
                      void onDayFilterChange("");
                      void onRoomFilterChange("");
                    }}
                    disabled={loadingAction !== null}
                    className={`${btnSecondary} self-end`}
                  >
                    Clear Filters
                  </button>
                ) : null}
              </div>

              {(() => {
                const allMeetings = getAllScheduledMeetings(
                  doctors,
                  doctorSchedulesMap,
                );
                const filteredMeetings = allMeetings.filter((meeting) => {
                  const matchesDay = dayFilter
                    ? meeting.day === dayFilter
                    : true;
                  const matchesRoom = roomFilter.trim()
                    ? (meeting.room_code || "")
                        .toLowerCase()
                        .includes(roomFilter.toLowerCase())
                    : true;
                  return matchesDay && matchesRoom;
                });

                if (!dayFilter && !roomFilter.trim()) return null;

                return (
                  <div className="space-y-2">
                    <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                      Filtered Classes
                    </h3>
                    <div className="overflow-auto rounded-lg border border-slate-700">
                      <table className="w-full min-w-[900px]">
                        <thead>
                          <tr className={tableHead}>
                            <th className="px-3 py-2.5 text-left">Day</th>
                            <th className="px-3 py-2.5 text-left">Time</th>
                            <th className="px-3 py-2.5 text-left">Course</th>
                            <th className="px-3 py-2.5 text-left">
                              Course Name
                            </th>
                            <th className="px-3 py-2.5 text-left">Section</th>
                            <th className="px-3 py-2.5 text-left">Type</th>
                            <th className="px-3 py-2.5 text-left">Room</th>
                            <th className="px-3 py-2.5 text-left">
                              Instructor
                            </th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-700/50">
                          {filteredMeetings.map((meeting, index) => (
                            <tr
                              key={`${meeting.section_id}-${meeting.day}-${index}`}
                              className="hover:bg-slate-700/20"
                            >
                              <td className={`${tdClass} text-slate-300`}>
                                {meeting.day}
                              </td>
                              <td className={`${tdClass} text-slate-300`}>
                                {formatTimeShort(meeting.start_time)} -{" "}
                                {formatTimeShort(meeting.end_time)}
                              </td>
                              <td
                                className={`${tdClass} text-indigo-300 font-medium`}
                              >
                                {meeting.course_code}
                              </td>
                              <td className={`${tdClass} text-slate-300`}>
                                {meeting.course_name}
                              </td>
                              <td
                                className={`${tdClass} text-slate-200 font-mono`}
                              >
                                {meeting.section_code}
                              </td>
                              <td className={`${tdClass} text-slate-400`}>
                                {meeting.section_type}
                              </td>
                              <td className={`${tdClass} text-cyan-300`}>
                                {meeting.room_code || "-"}
                              </td>
                              <td className={`${tdClass} text-slate-300`}>
                                {normalizeDoctorName(meeting.instructor)}
                              </td>
                            </tr>
                          ))}
                          {filteredMeetings.length === 0 ? (
                            <tr>
                              <td
                                colSpan={8}
                                className="px-3 py-5 text-center text-slate-500 text-xs"
                              >
                                No classes match current filters.
                              </td>
                            </tr>
                          ) : null}
                        </tbody>
                      </table>
                    </div>
                  </div>
                );
              })()}

              {!dayFilter && !roomFilter.trim() ? (
                <div className="overflow-auto rounded-lg border border-slate-700">
                  <table className="w-full">
                    <thead>
                      <tr className={tableHead}>
                        <th className="px-3 py-2.5 text-left">
                          <div className="flex items-center gap-2">
                            <span>Doctor Name</span>
                            <button
                              type="button"
                              onClick={onSortDoctorNameCycle}
                              title={
                                doctorNameSort === "none"
                                  ? "Sort A → Z"
                                  : doctorNameSort === "asc"
                                    ? "Sort Z → A"
                                    : "Clear sort"
                              }
                              className={`rounded p-1 transition-colors ${doctorNameSort !== "none" ? "text-indigo-400 bg-indigo-500/20" : "text-slate-500 hover:text-slate-300 hover:bg-slate-600/50"}`}
                            >
                              {doctorNameSort === "asc" ? (
                                <ArrowUp size={13} strokeWidth={2.5} />
                              ) : doctorNameSort === "desc" ? (
                                <ArrowDown size={13} strokeWidth={2.5} />
                              ) : (
                                <ArrowUpDown size={13} strokeWidth={2} />
                              )}
                            </button>
                          </div>
                        </th>
                        <th className="px-3 py-2.5 text-left">
                          <div className="flex items-center gap-2">
                            <span>Sections Count</span>
                            <button
                              type="button"
                              onClick={onSortDoctorCountCycle}
                              title={
                                doctorCountSort === "none"
                                  ? "Sort ascending"
                                  : doctorCountSort === "asc"
                                    ? "Sort descending"
                                    : "Clear sort"
                              }
                              className={`rounded p-1 transition-colors ${doctorCountSort !== "none" ? "text-indigo-400 bg-indigo-500/20" : "text-slate-500 hover:text-slate-300 hover:bg-slate-600/50"}`}
                            >
                              {doctorCountSort === "asc" ? (
                                <ArrowUp size={13} strokeWidth={2.5} />
                              ) : doctorCountSort === "desc" ? (
                                <ArrowDown size={13} strokeWidth={2.5} />
                              ) : (
                                <ArrowUpDown size={13} strokeWidth={2} />
                              )}
                            </button>
                          </div>
                        </th>
                        <th className="px-3 py-2.5 text-left">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-700/50">
                      {filteredDoctors.map((doctor) => (
                        <tr
                          key={doctor.instructor}
                          className={`transition-colors ${selectedDoctor === doctor.instructor ? "bg-indigo-900/20 border-l-2 border-indigo-500" : "hover:bg-slate-700/20"}`}
                        >
                          <td
                            className={`${tdClass} text-slate-200 font-medium`}
                          >
                            {normalizeDoctorName(doctor.instructor)}
                          </td>
                          <td className={`${tdClass}`}>
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-700/60 text-slate-300 border border-slate-600/50">
                              {doctor.sections_count}
                            </span>
                          </td>
                          <td className={`${tdClass}`}>
                            <button
                              type="button"
                              onClick={() =>
                                void onViewDoctorSchedule(doctor.instructor)
                              }
                              disabled={loadingAction !== null}
                              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed ${
                                selectedDoctor === doctor.instructor
                                  ? "bg-indigo-600 text-white shadow-lg shadow-indigo-900/40"
                                  : "bg-slate-700 hover:bg-indigo-600/80 text-slate-300 hover:text-white border border-slate-600 hover:border-indigo-500/60"
                              }`}
                            >
                              {loadingAction ===
                              `doctor-${doctor.instructor}` ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                <Eye size={12} />
                              )}
                              {loadingAction === `doctor-${doctor.instructor}`
                                ? "Loading"
                                : selectedDoctor === doctor.instructor
                                  ? "Viewing"
                                  : "View"}
                            </button>
                          </td>
                        </tr>
                      ))}
                      {filteredDoctors.length === 0 ? (
                        <tr>
                          <td
                            colSpan={3}
                            className="px-3 py-5 text-center text-slate-500 text-xs"
                          >
                            No doctors loaded yet.
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>

            {!dayFilter && !roomFilter.trim() ? (
              <div className={panelClass}>
                <div className="flex justify-between items-center flex-wrap gap-2">
                  <h2 className="text-sm font-semibold text-slate-300">
                    Selected Doctor Schedule
                  </h2>
                  <span className="text-xs text-slate-500">
                    {normalizeDoctorName(selectedDoctor) ||
                      "No doctor selected"}
                  </span>
                </div>

                {selectedDoctor && selectedDoctorSchedule ? (
                  <div className="flex gap-2 flex-wrap">
                    <button
                      type="button"
                      onClick={onExportDoctorPdf}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-rose-950/70 hover:bg-rose-900/70 border border-rose-600/40 hover:border-rose-400/60 text-rose-400 hover:text-rose-300 shadow-sm hover:shadow-rose-950/50"
                    >
                      <FileText size={13} />
                      Export as PDF
                    </button>
                    {selectedDoctor.trim().toUpperCase() !== "TBA" ? (
                      <button
                        type="button"
                        onClick={() => void onExportDoctorWord(selectedDoctor)}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-blue-950/70 hover:bg-blue-900/70 border border-blue-600/40 hover:border-blue-400/60 text-blue-400 hover:text-blue-300 shadow-sm hover:shadow-blue-950/50"
                      >
                        <FileType2 size={13} />
                        Export as Word
                      </button>
                    ) : null}
                  </div>
                ) : null}

                {!selectedDoctorSchedule || !selectedDoctor ? (
                  <p className="text-sm text-slate-500">
                    Select a doctor to view the schedule.
                  </p>
                ) : Object.keys(selectedDoctorSchedule).length === 0 ? (
                  <p className="text-sm text-slate-500">
                    No scheduled classes found for this doctor.
                  </p>
                ) : (
                  <>
                    {selectedDoctor?.trim().toUpperCase() === "TBA"
                      ? (() => {
                          const tbaAllMeetings = flattenDoctorSchedule(selectedDoctorSchedule);
                          const TBA_DAYS = ["MON", "TUE", "WED", "THU", "FRI"] as const;
                          type TbaDay = typeof TBA_DAYS[number];
                          const sectionEntries = new Map<number, {
                            section_id: number;
                            course_code: string;
                            course_name: string;
                            section_code: string;
                            section_type: string;
                            room_code?: string | null;
                            timeByDay: Map<TbaDay, { start: string; end: string }>;
                          }>();
                          for (const m of tbaAllMeetings) {
                            let entry = sectionEntries.get(m.section_id);
                            if (!entry) {
                              entry = {
                                section_id: m.section_id,
                                course_code: m.course_code,
                                course_name: m.course_name,
                                section_code: m.section_code,
                                section_type: m.section_type,
                                room_code: m.room_code,
                                timeByDay: new Map(),
                              };
                              sectionEntries.set(m.section_id, entry);
                            }
                            if ((TBA_DAYS as readonly string[]).includes(m.day)) {
                              entry.timeByDay.set(m.day as TbaDay, { start: m.start_time, end: m.end_time });
                            }
                          }
                          const tbaSections = Array.from(sectionEntries.values()).sort((a, b) =>
                            a.course_code.localeCompare(b.course_code) ||
                            a.section_code.localeCompare(b.section_code),
                          );
                          return (
                            <div className="space-y-2">
                              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                                All TBA Sections
                              </h3>
                              <div className="overflow-auto rounded-lg border border-slate-700">
                                <table className="w-full min-w-[900px]">
                                  <thead>
                                    <tr className={tableHead}>
                                      <th className="px-3 py-2.5 text-left">Course</th>
                                      <th className="px-3 py-2.5 text-left">Course Name</th>
                                      <th className="px-3 py-2.5 text-left">Section</th>
                                      <th className="px-3 py-2.5 text-left">Type</th>
                                      {TBA_DAYS.map((d) => (
                                        <th key={d} className="px-3 py-2.5 text-center text-slate-400">{d}</th>
                                      ))}
                                      <th className="px-3 py-2.5 text-left">Action</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-slate-700/50">
                                    {tbaSections.length === 0 ? (
                                      <tr>
                                        <td colSpan={10} className="px-3 py-5 text-center text-slate-500 text-xs">
                                          No TBA sections found.
                                        </td>
                                      </tr>
                                    ) : tbaSections.map((sec) => (
                                      <tr key={sec.section_id} className="hover:bg-slate-700/20">
                                        <td className={`${tdClass} text-indigo-300 font-medium`}>{sec.course_code}</td>
                                        <td className={`${tdClass} text-slate-300`}>{sec.course_name}</td>
                                        <td className={`${tdClass} text-slate-200 font-mono`}>{sec.section_code}</td>
                                        <td className={tdClass}>
                                          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                                            sec.section_type.toUpperCase() === "LECTURE"
                                              ? "bg-indigo-900/50 text-indigo-300"
                                              : sec.section_type.toUpperCase() === "LAB"
                                                ? "bg-cyan-900/50 text-cyan-300"
                                                : "bg-purple-900/50 text-purple-300"
                                          }`}>
                                            {sec.section_type}
                                          </span>
                                        </td>
                                        {TBA_DAYS.map((d) => {
                                          const t = sec.timeByDay.get(d);
                                          return (
                                            <td key={d} className={`${tdClass} text-center`}>
                                              {t ? (
                                                <span className="text-cyan-300 text-[11px] leading-tight whitespace-nowrap">
                                                  {formatTimeShort(t.start)}<br />{formatTimeShort(t.end)}
                                                </span>
                                              ) : (
                                                <span className="text-slate-700">—</span>
                                              )}
                                            </td>
                                          );
                                        })}
                                        <td className={tdClass}>
                                          <button
                                            type="button"
                                            onClick={() => {
                                              setEditingSectionId(sec.section_id);
                                              setEditingSectionType(sec.section_type);
                                              setSelectedSlot(null);
                                              setEditDays("");
                                              setEditStartTime("");
                                              setEditEndTime("");
                                              setEditRoomId("");
                                            }}
                                            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all bg-amber-950/70 hover:bg-amber-900/70 border border-amber-600/40 hover:border-amber-400/60 text-amber-400 hover:text-amber-300 shadow-sm hover:shadow-amber-950/50"
                                          >
                                            <Pencil size={11} />
                                            Edit
                                          </button>
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          );
                        })()
                      : getGroupedDoctorSchedule(selectedDoctorSchedule).map(
                          (group) => (
                            <div key={group.label} className="space-y-2">
                              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                                {group.label}
                              </h3>
                              <div className="overflow-auto rounded-lg border border-slate-700">
                                <table className="w-full min-w-[700px]">
                                  <thead>
                                    <tr className={tableHead}>
                                      <th className="px-3 py-2.5 text-left">Course Code</th>
                                      <th className="px-3 py-2.5 text-left">Course Name</th>
                                      <th className="px-3 py-2.5 text-left">Section</th>
                                      <th className="px-3 py-2.5 text-left">Type</th>
                                      <th className="px-3 py-2.5 text-left">Time</th>
                                      <th className="px-3 py-2.5 text-left">Room</th>
                                      <th className="px-3 py-2.5 text-left">Action</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-slate-700/50">
                                    {group.meetings.map((meeting) => (
                                      <tr
                                        key={`${group.label}-${meeting.section_id}-${meeting.start_time}`}
                                        className="hover:bg-slate-700/20"
                                      >
                                        <td className={`${tdClass} text-indigo-300 font-medium`}>{meeting.course_code}</td>
                                        <td className={`${tdClass} text-slate-300`}>{meeting.course_name}</td>
                                        <td className={`${tdClass} text-slate-200 font-mono`}>{meeting.section_code}</td>
                                        <td className={`${tdClass} text-slate-400`}>{meeting.section_type}</td>
                                        <td className={`${tdClass} text-cyan-300`}>
                                          {formatTimeShort(meeting.start_time)} -{" "}
                                          {formatTimeShort(meeting.end_time)}
                                        </td>
                                        <td className={`${tdClass} text-slate-300`}>{meeting.room_code || "-"}</td>
                                        <td className={tdClass}>
                                          <button
                                            type="button"
                                            onClick={() => {
                                              setEditingSectionId(meeting.section_id);
                                              setEditingSectionType(meeting.section_type);
                                              setSelectedSlot(null);
                                              setEditDays("");
                                              setEditStartTime("");
                                              setEditEndTime("");
                                              setEditRoomId("");
                                            }}
                                            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all bg-amber-950/70 hover:bg-amber-900/70 border border-amber-600/40 hover:border-amber-400/60 text-amber-400 hover:text-amber-300 shadow-sm hover:shadow-amber-950/50"
                                          >
                                            <Pencil size={11} />
                                            Edit
                                          </button>
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          ),
                        )}

                    {selectedDoctor?.trim().toUpperCase() === "TBA" &&
                      (() => {
                        const allMeetings = flattenDoctorSchedule(selectedDoctorSchedule);

                        const groupMap = new Map<string, {
                          groupKey: string;
                          courseCode: string;
                          courseName: string;
                          sectionIds: number[];
                          lectureCodes: string[];
                        }>();

                        for (const m of allMeetings) {
                          const gKey = tbaGroupKey(m.course_code, m.section_code);
                          let g = groupMap.get(gKey);
                          if (!g) {
                            g = { groupKey: gKey, courseCode: m.course_code, courseName: m.course_name, sectionIds: [], lectureCodes: [] };
                            groupMap.set(gKey, g);
                          }
                          if (!g.sectionIds.includes(m.section_id)) g.sectionIds.push(m.section_id);
                          if (m.section_type.trim().toUpperCase() === "LECTURE" && !g.lectureCodes.includes(m.section_code)) {
                            g.lectureCodes.push(m.section_code);
                          }
                        }

                        const courseGroups = Array.from(groupMap.values()).filter(
                          (g) => g.lectureCodes.length > 0,
                        );
                        if (courseGroups.length === 0) return null;

                        return (
                          <div className="bg-slate-900/50 border border-indigo-600/30 rounded-xl p-4 space-y-4">
                            <div>
                              <h3 className="text-sm font-semibold text-indigo-300">
                                Assign Instructor to Sections
                              </h3>
                              <p className="text-xs text-slate-500 mt-0.5">
                                Each row is a separate teaching group. &ldquo;Assign All&rdquo; sets the instructor for every section in that group.
                              </p>
                            </div>
                            <div className="space-y-4">
                              {courseGroups.map(({ groupKey, courseCode, courseName, sectionIds, lectureCodes }) => {
                                const inputValue = tbaCourseInputs[groupKey] ?? "";
                                const isAssigning = tbaCourseAssigningCode === groupKey;
                                const isBusy = tbaCourseAssigningCode !== null;
                                const doctorNames = Object.keys(doctorCoursesMap);
                                const suggestions = doctorNames.filter(
                                  (name) =>
                                    inputValue.length > 0 &&
                                    name.toLowerCase().includes(inputValue.toLowerCase()),
                                );
                                const hasBadPrefix =
                                  inputValue.length > 0 && !inputValue.startsWith("Dr. ");
                                const isKnownDoctor = Object.prototype.hasOwnProperty.call(doctorCoursesMap, inputValue);
                                const courseNotAllowed =
                                  isKnownDoctor && !doctorAllowedForCourse(inputValue, courseName, doctorCoursesMap);
                                const courseCount = getDoctorCourseCount(
                                  inputValue,
                                  tbaConfirmedAssignments,
                                );
                                const tooManyCourses =
                                  !hasBadPrefix &&
                                  inputValue.trim().length > 4 &&
                                  courseCount >= 2 &&
                                  !tbaConfirmedAssignments[groupKey];
                                const canAssign =
                                  inputValue.startsWith("Dr. ") &&
                                  inputValue.trim().length > 4 &&
                                  !isBusy;
                                return (
                                  <div
                                    key={groupKey}
                                    className="space-y-2 pb-4 border-b border-slate-700/40 last:border-0 last:pb-0"
                                  >
                                    <div className="flex items-start gap-2 flex-wrap">
                                      <span className="text-xs font-mono text-indigo-300 shrink-0">{courseCode}</span>
                                      <span className="text-xs text-slate-300 flex-1 min-w-0">{courseName}</span>
                                      <span className="text-[10px] font-mono text-slate-500 shrink-0">
                                        {lectureCodes.join(", ")}
                                      </span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                      <div className="relative flex-1">
                                        <input
                                          type="text"
                                          value={inputValue}
                                          onChange={(e) => {
                                            setTbaCourseInputs((prev) => ({
                                              ...prev,
                                              [groupKey]: e.target.value,
                                            }));
                                            setTbaSuggestionsOpen(groupKey);
                                          }}
                                          onFocus={() => {
                                            if (!inputValue) {
                                              setTbaCourseInputs((prev) => ({
                                                ...prev,
                                                [groupKey]: "Dr. ",
                                              }));
                                            }
                                            setTbaSuggestionsOpen(groupKey);
                                          }}
                                          onBlur={() => {
                                            setTimeout(() => setTbaSuggestionsOpen(null), 150);
                                          }}
                                          disabled={isBusy}
                                          placeholder="Dr. Name…"
                                          className="w-full bg-slate-800 border border-slate-600 text-slate-200 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-indigo-500 transition-colors disabled:opacity-50 placeholder:text-slate-600"
                                        />
                                        {tbaSuggestionsOpen === groupKey &&
                                          suggestions.length > 0 && (
                                            <div className="absolute top-full left-0 right-0 z-20 mt-1 bg-slate-800 border border-slate-600 rounded-lg shadow-xl overflow-hidden">
                                              {suggestions.map((name) => {
                                                const allowed = doctorAllowedForCourse(
                                                  name,
                                                  courseName,
                                                  doctorCoursesMap,
                                                );
                                                return (
                                                  <button
                                                    key={name}
                                                    type="button"
                                                    onMouseDown={(e) => {
                                                      e.preventDefault();
                                                      setTbaCourseInputs((prev) => ({
                                                        ...prev,
                                                        [groupKey]: name,
                                                      }));
                                                      setTbaSuggestionsOpen(null);
                                                    }}
                                                    className={`w-full text-left px-3 py-2 text-xs hover:bg-slate-700 transition-colors border-b border-slate-700/50 last:border-0 ${allowed ? "text-slate-200" : "text-amber-300/80"}`}
                                                  >
                                                    <div className="font-medium">{name}</div>
                                                    <div className="text-[10px] text-slate-500 mt-0.5 flex items-center gap-1">
                                                      {doctorCoursesMap[name]?.join(" · ")}
                                                      {!allowed && (
                                                        <span className="text-amber-400 ml-1">
                                                          ⚠ course mismatch
                                                        </span>
                                                      )}
                                                    </div>
                                                  </button>
                                                );
                                              })}
                                            </div>
                                          )}
                                      </div>
                                      <button
                                        type="button"
                                        onClick={() => void onAssignCourseInstructor(groupKey, sectionIds)}
                                        disabled={!canAssign}
                                        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-indigo-700/70 hover:bg-indigo-600/80 border border-indigo-500/40 text-white disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
                                      >
                                        {isAssigning ? (
                                          <Loader2 size={11} className="animate-spin" />
                                        ) : null}
                                        {isAssigning ? "Assigning…" : "Assign All"}
                                      </button>
                                    </div>
                                    {hasBadPrefix && (
                                      <p className="text-[10px] text-red-400">
                                        Name must start with &ldquo;Dr. &rdquo;
                                      </p>
                                    )}
                                    {!hasBadPrefix && courseNotAllowed && (
                                      <p className="text-[10px] text-amber-400">
                                        ⚠ {inputValue} is not listed for &ldquo;{courseName}&rdquo; in the Doctor Database.
                                      </p>
                                    )}
                                    {!hasBadPrefix && !courseNotAllowed && tooManyCourses && (
                                      <p className="text-[10px] text-amber-400">
                                        ⚠ {inputValue} is already assigned to {courseCount} course
                                        {courseCount !== 1 ? "s" : ""} this session (max 2).
                                      </p>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        );
                      })()}

                    {editingSectionId ? (
                      <div className="bg-slate-900/50 border border-amber-600/30 rounded-xl p-4 space-y-4">
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2">
                            <h3 className="text-sm font-semibold text-amber-300">
                              Manual Schedule Update
                            </h3>
                            {editingSectionType && (
                              <span
                                className={`text-xs font-medium px-2 py-0.5 rounded-full ${editingSectionType.toUpperCase() === "LAB" ? "bg-cyan-950/70 text-cyan-400 border border-cyan-700/40" : editingSectionType.toUpperCase() === "LECTURE" ? "bg-indigo-950/70 text-indigo-400 border border-indigo-700/40" : "bg-purple-950/70 text-purple-400 border border-purple-700/40"}`}
                              >
                                {editingSectionType}
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => void onSaveManualEdit()}
                              disabled={
                                !selectedSlot || loadingAction === "manual-edit"
                              }
                              className={`${btnPrimary}`}
                            >
                              {loadingAction === "manual-edit"
                                ? "Saving..."
                                : "Confirm Selection"}
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setEditingSectionId(null);
                                setEditingSectionType("");
                                setAvailableSlots([]);
                                setSelectedSlot(null);
                                setEditDays("");
                                setEditStartTime("");
                                setEditEndTime("");
                                setEditRoomId("");
                              }}
                              className={btnSecondary}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>

                        {selectedSlot && (
                          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-950/40 border border-amber-600/40 text-xs">
                            <span className="text-amber-400 font-semibold">
                              Selected:
                            </span>
                            <span className="text-amber-200">
                              {selectedSlot.days.replace(",", " / ")}
                            </span>
                            <span className="text-slate-400">·</span>
                            <span className="text-amber-200">
                              {selectedSlot.start_time} –{" "}
                              {selectedSlot.end_time}
                            </span>
                            <span className="text-slate-400">·</span>
                            <span className="text-amber-200">
                              Room {selectedSlot.room_code}
                            </span>
                          </div>
                        )}

                        {loadingAvailableSlots ? (
                          <div className="flex items-center justify-center gap-2 py-8 text-slate-500 text-xs">
                            <Loader2 size={14} className="animate-spin" />
                            Loading available slots...
                          </div>
                        ) : availableSlots.length === 0 ? (
                          <p className="text-xs text-slate-500 italic text-center py-6">
                            No available slots found for this section type.
                          </p>
                        ) : editingSectionType.toUpperCase() === "LECTURE" ? (
                          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            {Array.from(
                              new Set(availableSlots.map((s) => s.days)),
                            ).map((dayKey) => {
                              const label = dayKey
                                .split(",")
                                .join(" / ");
                              const slots = availableSlots.filter(
                                (s) => s.days === dayKey,
                              );
                              return (
                                <div key={dayKey} className="space-y-2">
                                  <div className="flex items-center gap-2">
                                    <span className="text-xs font-semibold text-indigo-400">
                                      {label}
                                    </span>
                                    <span className="text-[10px] text-slate-500">
                                      {slots.length} slot
                                      {slots.length !== 1 ? "s" : ""} available
                                    </span>
                                  </div>
                                  {slots.length === 0 ? (
                                    <p className="text-xs text-slate-600 italic">
                                      No slots available
                                    </p>
                                  ) : (
                                    <div className="space-y-1.5">
                                      {slots.map((slot) => {
                                        const isThisSlotSelected =
                                          selectedSlot?.days === slot.days &&
                                          selectedSlot?.start_time ===
                                            slot.start_time;
                                        return (
                                          <div
                                            key={`${slot.days}|${slot.start_time}`}
                                            className={`rounded-lg border p-2.5 space-y-2 transition-all ${isThisSlotSelected ? "border-amber-500/60 bg-amber-950/40" : "border-slate-700/50 bg-slate-800/30"}`}
                                          >
                                            <div className="flex items-center gap-2">
                                              <span
                                                className={`text-xs font-semibold ${isThisSlotSelected ? "text-amber-300" : "text-slate-200"}`}
                                              >
                                                {slot.start_time} –{" "}
                                                {slot.end_time}
                                              </span>
                                              <span className="text-[10px] text-slate-500">
                                                {slot.available_rooms.length}{" "}
                                                room
                                                {slot.available_rooms.length !==
                                                1
                                                  ? "s"
                                                  : ""}
                                              </span>
                                            </div>
                                            {renderRoomDropdown(
                                              slot,
                                              isThisSlotSelected,
                                            )}
                                          </div>
                                        );
                                      })}
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                            {Array.from(
                              new Set(availableSlots.map((s) => s.days)),
                            ).map((dayKey) => {
                              const dayLabels: Record<string, string> = {
                                MON: "Monday",
                                TUE: "Tuesday",
                                WED: "Wednesday",
                                THU: "Thursday",
                                FRI: "Friday",
                              };
                              const label = dayKey
                                .split(",")
                                .map((d) => dayLabels[d.trim()] ?? d.trim())
                                .join(" / ");
                              const slots = availableSlots.filter(
                                (s) => s.days === dayKey,
                              );
                              if (slots.length === 0) return null;
                              return (
                                <div key={dayKey} className="space-y-2">
                                  <div className="flex items-center gap-1.5">
                                    <span
                                      className={`text-xs font-semibold ${["LAB", "PRACTICAL"].includes(editingSectionType.toUpperCase()) ? "text-cyan-400" : "text-purple-400"}`}
                                    >
                                      {label}
                                    </span>
                                    <span className="text-[10px] text-slate-500">
                                      {slots.length}
                                    </span>
                                  </div>
                                  <div className="space-y-1.5">
                                    {slots.map((slot) => {
                                      const isThisSlotSelected =
                                        selectedSlot?.days === slot.days &&
                                        selectedSlot?.start_time ===
                                          slot.start_time;
                                      return (
                                        <div
                                          key={`${dayKey}|${slot.start_time}`}
                                          className={`rounded-lg border p-2 space-y-1.5 transition-all ${isThisSlotSelected ? "border-amber-500/60 bg-amber-950/40" : "border-slate-700/50 bg-slate-800/30"}`}
                                        >
                                          <span
                                            className={`block text-[11px] font-semibold ${isThisSlotSelected ? "text-amber-300" : "text-slate-200"}`}
                                          >
                                            {slot.start_time} –{" "}
                                            {slot.end_time}
                                          </span>
                                          <span className="text-[10px] text-slate-500">
                                            {slot.available_rooms.length} room
                                            {slot.available_rooms.length !== 1
                                              ? "s"
                                              : ""}
                                          </span>
                                          {renderRoomDropdown(
                                            slot,
                                            isThisSlotSelected,
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    ) : null}

                    <div className="bg-slate-900/50 border border-slate-700 rounded-xl p-4 space-y-3">
                      <div className="flex justify-between items-center">
                        <h3 className="text-sm font-semibold text-slate-300">
                          Doctor Weekly Schedule Grid
                        </h3>
                        <span className="text-xs text-slate-500">
                          {selectedDoctor}
                        </span>
                      </div>
                      <div className="timetable-wrapper">
                        <div className="time-axis">
                          <div className="time-axis-label"></div>
                          {[
                            "08:00",
                            "09:00",
                            "10:00",
                            "11:00",
                            "12:00",
                            "13:00",
                            "14:00",
                            "15:00",
                            "16:00",
                            "17:00",
                            "18:00",
                          ].map((t) => (
                            <div key={t} className="time-axis-label">
                              {t}
                            </div>
                          ))}
                        </div>
                        <div className="timetable-grid">
                          {DOCTOR_GRID_DAYS.map((day) => {
                            const dayMeetings = getDoctorGridMeetings(
                              selectedDoctorSchedule,
                            ).filter((m) => m.day === day);
                            return (
                              <div key={day} className="timetable-day-column">
                                <div className="timetable-day-head">
                                  {day === "MON"
                                    ? "Monday"
                                    : day === "TUE"
                                      ? "Tuesday"
                                      : day === "WED"
                                        ? "Wednesday"
                                        : day === "THU"
                                          ? "Thursday"
                                          : "Friday"}
                                </div>
                                <div
                                  className={`timetable-day-body ${day === "FRI" ? "timetable-day-body-friday" : ""}`}
                                  style={{ height: "660px" }}
                                >
                                  {dayMeetings.map((meeting, index) => (
                                    <div
                                      key={`${meeting.section_id}-${meeting.day}-${meeting.start_time}-${index}`}
                                      className="timetable-meeting"
                                      style={{
                                        top: `${meeting.top}px`,
                                        height: `${meeting.height}px`,
                                        left: "4px",
                                        right: "4px",
                                        background:
                                          meeting.section_type.toUpperCase() ===
                                          "LECTURE"
                                            ? "#4f46e5"
                                            : meeting.section_type.toUpperCase() ===
                                                "LAB"
                                              ? "#0891b2"
                                              : "#7c3aed",
                                      }}
                                    >
                                      <strong>{meeting.course_code}</strong>
                                      <span>{meeting.section_code}</span>
                                      <span>
                                        {formatTimeShort(meeting.start_time)} -{" "}
                                        {formatTimeShort(meeting.end_time)}
                                      </span>
                                      <span>{meeting.room_code || "-"}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </div>
            ) : null}
          </>
        ) : null}

        <WeeklyTimetable
          refreshKey={timetableRefreshKey}
          title="Weekly Schedule Grid"
          showBreak={activeScheduleType !== "summer"}
        />
      </div>
    </div>
  );
}
