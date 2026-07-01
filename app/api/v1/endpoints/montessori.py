"""
Montessori / Early-Years assessment endpoints.

Separate from /results because Creche–KG classes are rated on
developmental skills (1-3), not subject scores/averages/positions.
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.base import get_db
from app.models.models import (
    MontessoriReport, Student, Class, Session as SessionModel, Term,
    User, ResultStatus, AuditLog,
)
from app.api.v1.deps import get_current_user, get_current_student, require_staff, require_admin
from app.utils.montessori_data import (
    MONTESSORI_CATEGORIES, GRADING_KEY, MONTESSORI_CLASSES,
    is_montessori_class, validate_ratings,
)

router = APIRouter()


# ── Static config (no auth needed — just the form definition) ─────
@router.get("/categories")
def get_categories():
    return {
        "categories": MONTESSORI_CATEGORIES,
        "grading_key": GRADING_KEY,
        "montessori_classes": MONTESSORI_CLASSES,
    }


@router.get("/is-montessori-class")
def check_class(name: str):
    return {"class_name": name, "is_montessori": is_montessori_class(name)}


# ── Schemas ─────────────────────────────────────────────────────
class MontessoriIn(BaseModel):
    student_id: int
    session_id: int
    term_id: int
    ratings: dict = {}
    general_comment: Optional[str] = None
    class_teacher_name: Optional[str] = None
    class_teacher_report: Optional[str] = None
    pupils_conduct: Optional[str] = None
    proprietors_report: Optional[str] = None
    resumption_date: Optional[str] = None  # ISO date string


def _out(r: MontessoriReport) -> dict:
    return {
        "id": r.id,
        "student_id": r.student_id,
        "student_name": r.student.full_name if r.student else None,
        "class_id": r.class_id,
        "class_name": r.class_.name if r.class_ else None,
        "session_id": r.session_id,
        "session_name": r.session.session_name if r.session else None,
        "term_id": r.term_id,
        "term_name": r.term.term_name if r.term else None,
        "ratings": r.ratings or {},
        "general_comment": r.general_comment,
        "class_teacher_name": r.class_teacher_name,
        "class_teacher_report": r.class_teacher_report,
        "pupils_conduct": r.pupils_conduct,
        "proprietors_report": r.proprietors_report,
        "resumption_date": r.resumption_date.isoformat() if r.resumption_date else None,
        "status": r.status.value if r.status else None,
        "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
    }


# ── Staff: create / update (upsert per student+term) ──────────────
@router.post("")
def save_report(body: MontessoriIn, db: Session = Depends(get_db),
                 current_user: User = Depends(require_staff)):
    student = db.query(Student).filter(Student.id == body.student_id).first()
    if not student:
        raise HTTPException(404, "Student not found")
    if not is_montessori_class(student.class_.name if student.class_ else None):
        raise HTTPException(400, "This student's class does not use the Montessori report")

    existing = db.query(MontessoriReport).filter(
        MontessoriReport.student_id == body.student_id,
        MontessoriReport.term_id == body.term_id,
    ).first()

    resumption = None
    if body.resumption_date:
        try:
            resumption = datetime.fromisoformat(body.resumption_date)
        except ValueError:
            resumption = None

    cleaned_ratings = validate_ratings(body.ratings)

    if existing:
        existing.ratings = cleaned_ratings
        existing.general_comment = body.general_comment
        existing.class_teacher_name = body.class_teacher_name
        existing.class_teacher_report = body.class_teacher_report
        existing.pupils_conduct = body.pupils_conduct
        existing.proprietors_report = body.proprietors_report
        existing.resumption_date = resumption
        existing.entered_by = current_user.id
        existing.class_id = student.class_id
        existing.session_id = body.session_id
        db.commit()
        db.refresh(existing)
        report = existing
    else:
        report = MontessoriReport(
            student_id=body.student_id,
            class_id=student.class_id,
            session_id=body.session_id,
            term_id=body.term_id,
            ratings=cleaned_ratings,
            general_comment=body.general_comment,
            class_teacher_name=body.class_teacher_name,
            class_teacher_report=body.class_teacher_report,
            pupils_conduct=body.pupils_conduct,
            proprietors_report=body.proprietors_report,
            resumption_date=resumption,
            entered_by=current_user.id,
            status=ResultStatus.pending,
        )
        db.add(report)
        db.commit()
        db.refresh(report)

    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name, user_role=current_user.role.value,
        action="montessori_save", entity_type="montessori_report", entity_id=report.id,
        description=f"Saved Montessori report for {student.full_name}",
    ))
    db.commit()
    return _out(report)


# ── Staff: publish / lock a report so the student can see it ──────
@router.post("/{report_id}/publish")
def publish_report(report_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    r.status = ResultStatus.published
    r.approved_by = current_user.id
    r.approved_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Published", "id": r.id}


# ── Staff: list reports (filterable) ───────────────────────────────
@router.get("")
def list_reports(student_id: Optional[int] = None, class_id: Optional[int] = None,
                  session_id: Optional[int] = None, term_id: Optional[int] = None,
                  db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    q = db.query(MontessoriReport)
    if student_id: q = q.filter(MontessoriReport.student_id == student_id)
    if class_id:   q = q.filter(MontessoriReport.class_id == class_id)
    if session_id: q = q.filter(MontessoriReport.session_id == session_id)
    if term_id:    q = q.filter(MontessoriReport.term_id == term_id)
    items = q.order_by(MontessoriReport.uploaded_at.desc()).all()
    return {"items": [_out(r) for r in items]}


@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(require_staff)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    return _out(r)


# ── Student: view own report(s) — only published ones ─────────────
@router.get("/me/all")
def my_reports(session_id: Optional[int] = None, term_id: Optional[int] = None,
                db: Session = Depends(get_db), student=Depends(get_current_student)):
    if not is_montessori_class(student.class_.name if student.class_ else None):
        raise HTTPException(400, "Your class does not use the Montessori report")
    q = db.query(MontessoriReport).filter(
        MontessoriReport.student_id == student.id,
        MontessoriReport.status == ResultStatus.published,
    )
    if session_id: q = q.filter(MontessoriReport.session_id == session_id)
    if term_id:    q = q.filter(MontessoriReport.term_id == term_id)
    items = q.order_by(MontessoriReport.uploaded_at.desc()).all()
    return {"items": [_out(r) for r in items]}