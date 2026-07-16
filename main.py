"""
JAASIEL EDUCATION CENTRE — AI Result Management System
FastAPI Backend v3.0

Serves both the REST API and the frontend HTML files from /frontend/
Run with: uvicorn main:app --reload --port 8000
"""
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from app.core.config import settings
from app.api.v1.router import api_router
from app.db.base import Base, engine

# ── Create all DB tables on startup ───────────────────────────
# Import ALL models so Base knows about them
from app.models.models import (  # noqa
    User, Student, Class, Subject, ClassSubject,
    Session, Term, Result, ResultBatch, OcrJob, OcrRow,
    AuditLog, LoginSession, Notification, SchoolSettings,
    StudentLoginSession,
)
Base.metadata.create_all(bind=engine)

from app.scripts.fix_class_subjects import run_class_subject_fixes  # noqa
from app.scripts.sync_kg3_subjects import sync_kg3_subjects  # noqa -- REMOVE this import after one successful deploy
from app.scripts.list_class_subjects import list_class_subjects  # noqa -- DIAGNOSTIC ONLY, remove after checking logs
from app.scripts.add_single_result import add_single_result  # noqa -- ONE-OFF, remove after running once
from app.scripts.reset_sub_admin_password import reset_sub_admin_password  # noqa -- ONE-OFF, remove import + call below after confirming in logs

# ── FastAPI app ────────────────────────────────────────────────
app = FastAPI(
    title="Jaasiel RMS API",
    description="AI-powered Result Management System — Jaasiel Education Centre",
    version="3.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ── CORS ───────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── One-off data fixes (safe to leave in — no-ops after first run) ──
@app.on_event("startup")
def _startup_data_fixes():
    run_class_subject_fixes()
    sync_kg3_subjects()  # REMOVE this line after one successful deploy (see file docstring)
    list_class_subjects()  # DIAGNOSTIC ONLY -- prints current DB state, remove after checking logs
    add_single_result()  # ONE-OFF -- remove this line after confirming it worked in the logs
    reset_sub_admin_password()  # ONE-OFF -- remove this line + the import above after confirming the reset in logs

# ── API routes ─────────────────────────────────────────────────
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# ── Uploads folder ─────────────────────────────────────────────
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# ── Frontend static files ──────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")

if os.path.exists(FRONTEND_DIR) and os.listdir(FRONTEND_DIR):
    # Serve CSS/JS/images as static
    app.mount("/css",  StaticFiles(directory=os.path.join(FRONTEND_DIR, "css")),  name="css")
    app.mount("/js",   StaticFiles(directory=os.path.join(FRONTEND_DIR, "js")),   name="js")

    # Serve HTML pages
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(path: str):
        # Don't intercept API calls
        if path.startswith("api/") or path.startswith("uploads/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        full = os.path.join(FRONTEND_DIR, path)
        if os.path.isfile(full):
            return FileResponse(full)
        # Fallback to login
        return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))
else:
    @app.get("/", include_in_schema=False)
    async def no_frontend():
        return JSONResponse({
            "service": "Jaasiel RMS API",
            "version": "3.0.0",
            "status": "running",
            "docs": "/api/docs",
            "note": "Copy your frontend HTML files into the /frontend/ directory to serve them here."
        })

# ── Health check ───────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Jaasiel RMS API", "version": "3.0.0"}


@app.get("/api/debug-cors")
def debug_cors():
    return {
        "allowed_origins": settings.allowed_origins_list,
        "env_value": os.getenv("ALLOWED_ORIGINS", "NOT SET")
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)