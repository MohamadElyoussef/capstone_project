from app.db.models.audit_log import AuditLog
from app.db.models.completed_course import CompletedCourse
from app.db.models.course import Course
from app.db.models.course_prerequisite import CoursePrerequisite
from app.db.models.enrollment import Enrollment
from app.db.models.registration_setting import RegistrationSetting
from app.db.models.room import Room
from app.db.models.scheduled_meeting import ScheduledMeeting
from app.db.models.section import Section
from app.db.models.study_plan_mapping import StudyPlanMapping
from app.db.models.user import User

__all__ = [
    "AuditLog",
    "CompletedCourse",
    "Course",
    "CoursePrerequisite",
    "Enrollment",
    "RegistrationSetting",
    "Room",
    "ScheduledMeeting",
    "Section",
    "StudyPlanMapping",
    "User",
]