import { clearAuthStorage, getStoredToken } from "../lib/auth";

const DEFAULT_API_BASE_URL = "/api/v1";
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL;

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

interface RequestOptions {
  method?: HttpMethod;
  body?: unknown;
  token?: string | null;
  skipAuth?: boolean;
  signal?: AbortSignal;
}

interface ErrorBody {
  detail?: string;
}

export type UserRole = "ADMIN";

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export interface CurrentUser {
  id: number;
  username: string;
  full_name: string | null;
  role: UserRole | string;
  is_active: boolean;
}

export interface AdminScheduleMeeting {
  meeting_id: number;
  section_id: number;
  section_code: string;
  section_type: string;
  course_id: number;
  course_code: string;
  course_name: string;
  instructor: string | null;
  room_id: number | null;
  room_code: string | null;
  day: string;
  start_time: string;
  end_time: string;
}

export type WeeklyScheduleResponse = Record<string, AdminScheduleMeeting[]>;

export interface UnscheduledSection {
  section_id: number;
  section_code: string;
  section_type: string;
  course_code: string;
  course_name: string;
  instructor: string;
  gender_allowed: string;
  reason: string;
}

export interface GenerateScheduleResponse {
  total_sections: number;
  scheduled_sections: number;
  conflicts_found: number;
  unscheduled_sections: UnscheduledSection[];
}

export interface SuggestionEntry {
  type: "CHANGE_INSTRUCTOR" | "CHANGE_ROOM" | "CHANGE_TIME" | "CHANGE_BOTH" | string;
  message: string;
  payload: Record<string, unknown>;
}

export interface SectionSuggestion {
  section_id: number;
  section_code: string;
  reason: string;
  suggestions: SuggestionEntry[];
}

export interface AdminImportSummary {
  rooms_imported: number;
  courses_imported: number;
  sections_imported: number;
  lecture_rooms_count: number;
  lab_rooms_count: number;
}

export interface RegistrationStatusResponse {
  is_open: boolean;
}

export interface AuditLogEntry {
  id: number;
  timestamp: string;
  actor_user_id: number | null;
  actor_username: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  before_data: Record<string, unknown> | null;
  after_data: Record<string, unknown> | null;
}

export interface DoctorListItem {
  instructor: string;
  sections_count: number;
}

export interface DoctorScheduleMeeting {
  section_id: number;
  section_code: string;
  section_type: string;
  course_code: string;
  course_name: string;
  day: string;
  start_time: string;
  end_time: string;
  room_code: string | null;
}

export type DoctorScheduleResponse = Record<string, DoctorScheduleMeeting[]>;

export interface ScheduleConflict {
  day: string;
  overlap_start: string;
  overlap_end: string;
  first_meeting_id: number;
  second_meeting_id: number;
  first_section_id: number;
  first_section_code: string;
  second_section_id: number;
  second_section_code: string;
  room_id?: number | null;
  room_code?: string | null;
  instructor?: string | null;
}

export interface ScheduleConflictReportResponse {
  room_conflicts: ScheduleConflict[];
  instructor_conflicts: ScheduleConflict[];
}

export interface RoomOption {
  id: number;
  room_code: string;
  room_type?: string | null;
  capacity?: number;
}

export interface RoomBookingEntry {
  day: string;
  start_time: string;
  end_time: string;
  section_code: string;
}

export interface RoomAvailabilityEntry {
  id: number;
  room_code: string;
  room_type: string | null;
  bookings: RoomBookingEntry[];
  is_free: boolean;
}

export interface AvailableSlotRoom {
  id: number;
  room_code: string;
}

export interface AvailableSlot {
  days: string;
  start_time: string;
  end_time: string;
  available_rooms: AvailableSlotRoom[];
}

export interface ManualScheduleUpdateRequest {
  days: string;
  start_time: string;
  end_time: string;
  room_code: string | null;
}

export interface ManualScheduleUpdateResponse {
  message: string;
  section_id: number;
  days: string;
  start_time: string;
  end_time: string;
  room_id: number | null;
  room_code: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

function buildUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = options.token ?? getStoredToken();
  const headers = new Headers();
  const isFormData = options.body instanceof FormData;

  if (options.body !== undefined && !isFormData) {
    headers.set("Content-Type", "application/json");
  }
  if (!options.skipAuth && token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(buildUrl(path), {
    method: options.method ?? "GET",
    headers,
    body:
      options.body === undefined
        ? undefined
        : isFormData
          ? (options.body as FormData)
          : JSON.stringify(options.body),
    signal: options.signal,
  });

  const text = await response.text();
  let parsed: unknown = undefined;
  if (text.length > 0) {
    try {
      parsed = JSON.parse(text) as unknown;
    } catch {
      parsed = undefined;
    }
  }

  if (!response.ok) {
    let detail = response.statusText || "Request failed";
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const body = parsed as ErrorBody;
      if (typeof body.detail === "string") {
        detail = body.detail;
      }
    }
    if (response.status === 401) {
      clearAuthStorage();
    }
    throw new ApiError(response.status, detail);
  }

  return parsed as T;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>("/auth/login", {
    method: "POST",
    body: { username, password },
    skipAuth: true,
  });
}

export async function getCurrentUser(token?: string): Promise<CurrentUser> {
  return request<CurrentUser>("/users/me", {
    method: "GET",
    token: token ?? null,
  });
}

export async function generateSchedule(lectureLimit: number = 10, tutorialLimit: number = 8): Promise<GenerateScheduleResponse> {
  return request<GenerateScheduleResponse>("/admin/schedule/generate", {
    method: "POST",
    body: { lecture_limit: lectureLimit, tutorial_limit: tutorialLimit },
  });
}

export async function generateSummerSchedule(lectureLimit: number = 5, tutorialLimit: number = 4, labLimit: number = 6): Promise<GenerateScheduleResponse> {
  return request<GenerateScheduleResponse>("/admin/schedule/generate-summer", {
    method: "POST",
    body: { lecture_limit: lectureLimit, tutorial_limit: tutorialLimit, lab_limit: labLimit },
  });
}

export async function getActiveScheduleType(): Promise<{ schedule_type: "regular" | "summer" }> {
  return request<{ schedule_type: "regular" | "summer" }>("/admin/schedule/active-type");
}

export async function updateSectionInstructor(
  sectionId: number,
  instructorName: string,
): Promise<{ section_id: number; instructor: string }> {
  return request<{ section_id: number; instructor: string }>(
    `/admin/schedule/sections/${sectionId}/instructor`,
    { method: "PATCH", body: { instructor_name: instructorName } },
  );
}

export async function getDoctorCoursesMap(): Promise<Record<string, string[]>> {
  return request<Record<string, string[]>>("/admin/schedule/doctor-courses");
}

export async function bulkUpdateSectionInstructor(
  sectionIds: number[],
  instructorName: string,
): Promise<{ sections_updated: number; instructor: string }> {
  return request<{ sections_updated: number; instructor: string }>(
    "/admin/schedule/sections/bulk-instructor",
    { method: "PATCH", body: { section_ids: sectionIds, instructor_name: instructorName } },
  );
}

export async function updateCourseInstructor(
  courseCode: string,
  instructorName: string,
): Promise<{ course_code: string; instructor: string; sections_updated: number }> {
  return request<{ course_code: string; instructor: string; sections_updated: number }>(
    `/admin/schedule/courses/${encodeURIComponent(courseCode)}/instructor`,
    { method: "PATCH", body: { instructor_name: instructorName } },
  );
}

export async function getAdminSchedule(): Promise<WeeklyScheduleResponse> {
  return request<WeeklyScheduleResponse>("/admin/schedule");
}

export async function getUnscheduledSections(): Promise<UnscheduledSection[]> {
  return request<UnscheduledSection[]>("/admin/schedule/unscheduled");
}

export async function getSuggestions(): Promise<SectionSuggestion[]> {
  return request<SectionSuggestion[]>("/admin/schedule/suggestions");
}

export async function getScheduleConflicts(): Promise<ScheduleConflictReportResponse> {
  return request<ScheduleConflictReportResponse>("/admin/schedule/conflicts");
}

export async function getAdminRegistrationStatus(): Promise<RegistrationStatusResponse> {
  return request<RegistrationStatusResponse>("/admin/schedule/registration-status");
}

export async function openAdminRegistration(): Promise<RegistrationStatusResponse> {
  return request<RegistrationStatusResponse>("/admin/schedule/registration/open", {
    method: "POST",
  });
}

export async function closeAdminRegistration(): Promise<RegistrationStatusResponse> {
  return request<RegistrationStatusResponse>("/admin/schedule/registration/close", {
    method: "POST",
  });
}

export async function getAuditLogs(): Promise<AuditLogEntry[]> {
  return request<AuditLogEntry[]>("/admin/schedule/audit-logs");
}

export async function getDoctors(): Promise<DoctorListItem[]> {
  return request<DoctorListItem[]>("/admin/schedule/doctors");
}

export interface AvailableInstructor {
  instructor: string;
  max_daily_load: number;
}

export async function getAvailableInstructors(sectionId: number): Promise<AvailableInstructor[]> {
  return request<AvailableInstructor[]>(`/admin/schedule/sections/${sectionId}/available-instructors`);
}

export async function getDoctorSchedule(instructorName: string): Promise<DoctorScheduleResponse> {
  return request<DoctorScheduleResponse>(
    `/admin/schedule/doctors/${encodeURIComponent(instructorName)}/schedule`
  );
}

export async function getRooms(sectionType?: string): Promise<RoomOption[]> {
  const url = sectionType
    ? `/admin/schedule/rooms?section_type=${encodeURIComponent(sectionType)}`
    : "/admin/schedule/rooms";
  return request<RoomOption[]>(url);
}

export async function getAvailableRooms(params: {
  sectionType?: string;
  days?: string;
  startTime?: string;
  endTime?: string;
  excludeSectionId?: number;
}): Promise<RoomAvailabilityEntry[]> {
  const query = new URLSearchParams();
  if (params.sectionType) query.set("section_type", params.sectionType);
  if (params.days) query.set("days", params.days);
  if (params.startTime) query.set("start_time", params.startTime);
  if (params.endTime) query.set("end_time", params.endTime);
  if (params.excludeSectionId != null) query.set("exclude_section_id", String(params.excludeSectionId));
  const qs = query.toString();
  return request<RoomAvailabilityEntry[]>(`/admin/schedule/rooms/available${qs ? `?${qs}` : ""}`);
}

export async function getAvailableSlots(sectionId: number, scheduleType?: string): Promise<AvailableSlot[]> {
  const qs = scheduleType ? `?schedule_type=${encodeURIComponent(scheduleType)}` : "";
  return request<AvailableSlot[]>(`/admin/schedule/sections/${sectionId}/available-slots${qs}`);
}

export async function manualUpdateSectionSchedule(
  sectionId: number,
  payload: ManualScheduleUpdateRequest,
): Promise<ManualScheduleUpdateResponse> {
  return request<ManualScheduleUpdateResponse>(
    `/admin/schedule/sections/${sectionId}/manual-update`,
    {
      method: "PATCH",
      body: payload,
    }
  );
}

export async function getDataStatus(): Promise<{ sections_count: number; rooms_count: number }> {
  return request<{ sections_count: number; rooms_count: number }>("/admin/schedule/data-status");
}

export async function importUniversityData(
  roomsFile: File | null,
  coursesFile: File,
): Promise<AdminImportSummary> {
  const formData = new FormData();
  if (roomsFile) formData.append("rooms_file", roomsFile);
  formData.append("courses_file", coursesFile);
  return request<AdminImportSummary>("/admin/import", {
    method: "POST",
    body: formData,
  });
}

