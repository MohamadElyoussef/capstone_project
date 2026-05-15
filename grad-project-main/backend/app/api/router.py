from fastapi import APIRouter

from app.api.admin_import import router as admin_import_router
from app.api.admin_registration import router as admin_registration_router
from app.api.admin_schedule import router as admin_schedule_router
from app.api.admin_sections import router as admin_sections_router
from app.api.auth import router as auth_router
from app.api.courses import router as courses_router
from app.api.health import router as health_router
from app.api.rooms import router as rooms_router
from app.api.sections import router as sections_router
from app.api.users import router as users_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(courses_router, prefix="/courses", tags=["courses"])
api_router.include_router(rooms_router, prefix="/rooms", tags=["rooms"])
api_router.include_router(sections_router, prefix="/sections", tags=["sections"])
api_router.include_router(
    admin_registration_router,
    prefix="/admin/registration",
    tags=["admin-registration"],
)
api_router.include_router(
    admin_schedule_router,
    prefix="/admin/schedule",
    tags=["admin-schedule"],
)
api_router.include_router(
    admin_sections_router,
    prefix="/admin/sections",
    tags=["admin-sections"],
)
api_router.include_router(
    admin_import_router,
    prefix="/admin",
    tags=["admin-import"],
)
