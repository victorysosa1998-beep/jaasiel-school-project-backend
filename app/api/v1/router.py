from fastapi import APIRouter
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.students import router as students_router
from app.api.v1.endpoints.results import router as results_router
from app.api.v1.endpoints.ocr import router as ocr_router
from app.api.v1.endpoints.other import (
    dashboard_router, sessions_router, analytics_router,
    classes_router, subjects_router, audit_router,
    notifications_router, settings_router, reports_router,
)

api_router = APIRouter()

api_router.include_router(auth_router,          prefix="/auth",          tags=["Auth"])
api_router.include_router(dashboard_router,     prefix="/dashboard",     tags=["Dashboard"])
api_router.include_router(students_router,      prefix="/students",      tags=["Students"])
api_router.include_router(results_router,       prefix="/results",       tags=["Results"])
api_router.include_router(ocr_router,           prefix="/ocr",           tags=["OCR"])
api_router.include_router(sessions_router,      prefix="/sessions",      tags=["Sessions"])
api_router.include_router(analytics_router,     prefix="/analytics",     tags=["Analytics"])
api_router.include_router(classes_router,       prefix="/classes",       tags=["Classes"])
api_router.include_router(subjects_router,      prefix="/subjects",      tags=["Subjects"])
api_router.include_router(audit_router,         prefix="/audit-logs",    tags=["Audit"])
api_router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(reports_router,       prefix="/reports",       tags=["Reports"])
api_router.include_router(settings_router,      prefix="",               tags=["Settings"])
