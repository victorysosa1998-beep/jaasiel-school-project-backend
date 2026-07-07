"""
Montessori / Early-Years assessment endpoints.

Separate from /results because Creche/Daycare/Pre-Nursery classes are rated
on developmental skills (1-3), not subject scores/averages/positions.

Workflow (mirrors /results):
  Sub Admin: save draft (pending) -> submit / submit-class -> submitted
  Admin:     approve / reject / correction -> approved / rejected / correction_requested
  Admin:     publish -> student can view
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.base import get_db
from app.models.models import (
    MontessoriReport, Student, Class, Session as SessionModel, Term,
    User, UserRole, ResultStatus, AuditLog, Notification,
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
    resumption_date: Optional[str] = None  # ISO date string (unused now — kept for compat)


def _normalize_class(n: str) -> str:
    """Lowercase + remove all spaces for fuzzy class name matching."""
    return (n or "").strip().lower().replace(" ", "")


def _rating_avg(ratings: dict) -> float:
    """Average of all rated (1-3) skill items across every category."""
    total = 0
    s = 0.0
    for cat in (ratings or {}).values():
        for v in (cat or {}).values():
            if v is not None:
                total += 1
                s += float(v)
    return (s / total) if total else 0.0


def _rating_category_avgs(ratings: dict) -> dict:
    """Average rating per category, skipping categories with no ratings yet."""
    out = {}
    for cat, items in (ratings or {}).items():
        vals = [float(v) for v in (items or {}).values() if v is not None]
        if vals:
            out[cat] = sum(vals) / len(vals)
    return out


def _auto_class_teacher_report(ratings: dict) -> str:
    """
    Auto-generate the Class Teacher's Report from the pupil's actual
    ratings this term — this is the single source of truth (never typed
    in manually, and recomputed server-side on every save/publish so it
    can never go stale relative to the ratings actually on file).

    Calls out the pupil's strongest/weakest category so pupils in the
    same overall band don't all read identical wording.
    """
    avg = _rating_avg(ratings)
    if not avg:
        return "Not yet assessed for this term."

    cat_avgs  = _rating_category_avgs(ratings)
    strongest = max(cat_avgs, key=cat_avgs.get) if cat_avgs else None
    weakest   = min(cat_avgs, key=cat_avgs.get) if cat_avgs else None

    if avg >= 2.8:
        text = "Outstanding developmental progress this term."
        if strongest:
            text += f" {strongest} was a particular highlight."
        return text + " Keep up the excellent work."
    if avg >= 2.4:
        text = "Excellent developmental progress this term."
        if strongest:
            text += f" {strongest} stood out as a strength."
        return text + " Keep up the good work."
    if avg >= 2.0:
        text = "Good, steady progress this term."
        if weakest and cat_avgs[weakest] < 2.0:
            text += f" A little more practice with {weakest.lower()} would help."
        return text + " Continue practicing these skills at home."
    if avg >= 1.5:
        text = "Fair progress this term, with room to grow."
        if weakest:
            text += f" {weakest} needs the most encouragement."
        return text
    text = "Needs more support and practice with these skills next term."
    if weakest:
        text += f" {weakest} in particular."
    return text


def _auto_proprietors_report(ratings: dict) -> str:
    """
    Auto-generate the Proprietor's Report — shorter and more formal than
    the class teacher's report, and worded distinctly per band so pupils
    at the same average don't read identical text in both fields.
    """
    avg = _rating_avg(ratings)
    if not avg:
        return "Not yet assessed for this term."
    if avg >= 2.8:
        return "An outstanding term. Well done — keep up this standard."
    if avg >= 2.4:
        return "Commendable performance this term. Well done."
    if avg >= 2.0:
        return "Satisfactory progress overall. Keep encouraging your ward at home."
    if avg >= 1.5:
        return "Fair progress. More encouragement at home would help."
    return "Requires more attention and encouragement at home to strengthen these skills."


def _resolve_term_settings(db: Session, term, cache: Optional[dict] = None):
    """
    Mirror the live Term-based resumption-date / next-term-fee lookup used
    for the regular (subject-based) report card in students.py, so the
    Montessori report always reflects the school-wide Term settings instead
    of a per-report copy.

    Falls back to sibling terms in the same session if this term has no
    resumption_date / next_term_fee saved (admin may have set it on a
    different term of the same session).

    `cache` is an optional dict (keyed by term_id) shared across a single
    request — list/bulk endpoints render many reports at once and would
    otherwise re-run the sibling-term query once per report.
    """
    if not term:
        return None, {}

    if cache is not None and term.id in cache:
        return cache[term.id]

    resumption = term.resumption_date
    fees = term.next_term_fee

    if not resumption or not fees:
        siblings = db.query(Term).filter(
            Term.session_id == term.session_id,
            Term.id != term.id,
        ).all()
        for sib in siblings:
            if not resumption and sib.resumption_date:
                resumption = sib.resumption_date
            if not fees and sib.next_term_fee:
                fees = sib.next_term_fee
            if resumption and fees:
                break

    result = (resumption, fees or {})
    if cache is not None:
        cache[term.id] = result
    return result


def _class_fee(fees: dict, class_name: str):
    """
    Look up a class's fee from the Term's next_term_fee dict.
    Uses explicit None checks (not `or` chaining) so a legitimately-set
    fee of 0 is still returned instead of being treated as "not found"
    and falling through to the next lookup strategy.
    """
    if not fees or not class_name:
        return None

    def _first_match(candidates):
        for v in candidates:
            if v is not None:
                return v
        return None

    cn_norm = _normalize_class(class_name)
    exact = fees.get(class_name)
    if exact is not None:
        return exact
    stripped = fees.get(class_name.strip())
    if stripped is not None:
        return stripped
    ci_match = _first_match(
        v for k, v in fees.items() if k.strip().lower() == class_name.strip().lower()
    )
    if ci_match is not None:
        return ci_match
    norm_match = _first_match(
        v for k, v in fees.items() if _normalize_class(k) == cn_norm
    )
    if norm_match is not None:
        return norm_match
    return fees.get("all")


def _out(r: MontessoriReport, db: Optional[Session] = None, cache: Optional[dict] = None) -> dict:
    class_name = r.class_.name if r.class_ else None

    resumption_date, fees = (None, {})
    if db is not None:
        resumption_date, fees = _resolve_term_settings(db, r.term, cache)
    else:
        resumption_date = r.term.resumption_date if r.term else None
        fees = (r.term.next_term_fee if r.term else None) or {}
    next_term_fee = _class_fee(fees, class_name)

    return {
        "id": r.id,
        "student_id": r.student_id,
        "student_name": r.student.full_name if r.student else None,
        "class_id": r.class_id,
        "class_name": class_name,
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
        # Live from the shared Term record (same as the regular report card),
        # falling back to the report's own stored value for old data.
        "resumption_date": (resumption_date or r.resumption_date).isoformat() if (resumption_date or r.resumption_date) else None,
        "next_term_fee": next_term_fee,
        "status": r.status.value if r.status else None,
        "admin_note": r.admin_note,
        "correction_comment": r.admin_note,   # alias so frontend list code can reuse the results pattern
        "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        # The date the report was actually published to the student —
        # distinct from uploaded_at (when a sub admin first saved a draft).
        "published_at": r.approved_at.isoformat() if (r.status == ResultStatus.published and r.approved_at) else None,
        "entered_by": r.entered_by,
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

    # Once approved/locked/published, sub-admin cannot silently overwrite
    if existing and existing.status in (ResultStatus.approved, ResultStatus.locked, ResultStatus.published):
        raise HTTPException(403,
            "This report has already been approved/published and can no longer be edited. "
            "Contact Super Admin if changes are needed.")

    resumption = None
    if body.resumption_date:
        try:
            resumption = datetime.fromisoformat(body.resumption_date)
        except ValueError:
            resumption = None

    cleaned_ratings = validate_ratings(body.ratings)
    auto_teacher_report = _auto_class_teacher_report(cleaned_ratings)
    auto_proprietors_report = _auto_proprietors_report(cleaned_ratings)

    if existing:
        existing.ratings = cleaned_ratings
        existing.general_comment = body.general_comment
        existing.class_teacher_name = body.class_teacher_name
        existing.class_teacher_report = auto_teacher_report
        existing.pupils_conduct = body.pupils_conduct
        existing.proprietors_report = auto_proprietors_report
        existing.resumption_date = resumption
        existing.entered_by = current_user.id
        existing.class_id = student.class_id
        existing.session_id = body.session_id
        # Editing a submitted/rejected/correction report resets it to draft — admin re-reviews
        if existing.status in (ResultStatus.submitted, ResultStatus.correction_requested, ResultStatus.rejected):
            existing.status = ResultStatus.pending
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
            class_teacher_report=auto_teacher_report,
            pupils_conduct=body.pupils_conduct,
            proprietors_report=auto_proprietors_report,
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
    return _out(report, db)


# ── Sub Admin: submit a single report for admin review ────────────
@router.post("/{report_id}/submit")
def submit_report(report_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    if r.status in (ResultStatus.approved, ResultStatus.locked, ResultStatus.published):
        raise HTTPException(403, "This report has already been approved/published.")
    r.status = ResultStatus.submitted
    db.commit()

    admins = db.query(User).filter(
        User.role.in_([UserRole.super_admin, UserRole.admin]),
        User.is_active == True
    ).all()
    for admin in admins:
        db.add(Notification(
            user_id=admin.id, type="upload",
            title="Montessori Report Ready for Review",
            message=f"{current_user.full_name} submitted a report for "
                    f"{r.student.full_name if r.student else 'a student'} "
                    f"— {r.term.term_name if r.term else ''}."
        ))
    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name, user_role=current_user.role.value,
        action="montessori_submit", entity_type="montessori_report", entity_id=r.id,
        description=f"Submitted Montessori report for {r.student.full_name if r.student else report_id}",
    ))
    db.commit()
    return {"message": "Submitted to admin for approval.", "id": r.id}


# ── Sub Admin: submit ALL pending Montessori reports for a class+term ──
@router.post("/submit-class")
def submit_class_reports(body: dict, db: Session = Depends(get_db),
                          current_user: User = Depends(require_staff)):
    class_id = body.get("class_id")
    term_id  = body.get("term_id")
    if not class_id or not term_id:
        raise HTTPException(400, "class_id and term_id required")

    reports = db.query(MontessoriReport).filter(
        MontessoriReport.class_id == class_id,
        MontessoriReport.term_id  == term_id,
        MontessoriReport.entered_by == current_user.id,
        MontessoriReport.status == ResultStatus.pending,
    ).all()
    if not reports:
        raise HTTPException(400, "No draft reports found for this class/term.")

    for r in reports:
        r.status = ResultStatus.submitted

    admins = db.query(User).filter(
        User.role.in_([UserRole.super_admin, UserRole.admin]),
        User.is_active == True
    ).all()
    for admin in admins:
        db.add(Notification(
            user_id=admin.id, type="upload",
            title="Montessori Reports Ready for Review",
            message=f"{current_user.full_name} submitted {len(reports)} report(s) for review."
        ))
    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name, user_role=current_user.role.value,
        action="montessori_submit_class", entity_type="montessori_report",
        description=f"Submitted {len(reports)} Montessori reports for class {class_id}, term {term_id}"
    ))
    db.commit()
    return {"message": f"{len(reports)} report(s) submitted to admin.", "submitted": len(reports)}


# ── Sub Admin: my own submissions (history — powers "My Uploads") ─
@router.get("/subadmin/uploads")
def subadmin_uploads(page: int = 1, per_page: int = 20, status: Optional[str] = None,
                      class_id: Optional[int] = None, term_id: Optional[int] = None,
                      db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    q = db.query(MontessoriReport).filter(MontessoriReport.entered_by == current_user.id)
    if status:
        try: q = q.filter(MontessoriReport.status == ResultStatus(status))
        except ValueError: pass
    if class_id: q = q.filter(MontessoriReport.class_id == class_id)
    if term_id:  q = q.filter(MontessoriReport.term_id == term_id)
    total = q.count()
    items = q.order_by(MontessoriReport.uploaded_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    _term_cache = {}
    return {"items": [_out(r, db, _term_cache) for r in items], "total": total}


# ── Admin: list submitted reports awaiting review ──────────────────
@router.get("/pending")
def pending_reports(page: int = 1, per_page: int = 20,
                     class_id: Optional[int] = None, class_name: Optional[str] = None,
                     term_id: Optional[int] = None,
                     db: Session = Depends(get_db), current_user: User = Depends(require_admin)):

    def base_query():
        q = db.query(MontessoriReport)
        if class_id:
            q = q.filter(MontessoriReport.class_id == class_id)
        if class_name:
            q = q.join(Class, MontessoriReport.class_id == Class.id) \
                 .filter(Class.name.ilike(class_name))
        if term_id:
            q = q.filter(MontessoriReport.term_id == term_id)
        return q

    q = base_query().filter(MontessoriReport.status == ResultStatus.submitted)
    total = q.count()
    items = q.order_by(MontessoriReport.uploaded_at.desc()).offset((page-1)*per_page).limit(per_page).all()

    approved_count = base_query().filter(MontessoriReport.status == ResultStatus.approved).count()
    correction_count = base_query().filter(MontessoriReport.status == ResultStatus.correction_requested).count()

    _term_cache = {}
    return {
        "items": [_out(r, db, _term_cache) for r in items],
        "total": total,
        "approved_count": approved_count,
        "correction_count": correction_count,
    }


# ── Admin: approve ──────────────────────────────────────────────────
@router.post("/{report_id}/approve")
def approve_report(report_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    if r.status == ResultStatus.locked:
        raise HTTPException(403, "Cannot approve a locked report")
    r.status = ResultStatus.approved
    r.approved_by = current_user.id
    r.approved_at = datetime.now(timezone.utc)
    db.add(Notification(
        user_id=r.entered_by, type="approved", title="Montessori Report Approved",
        message=f"{r.student.full_name if r.student else ''} — report approved. "
                "Use Publish to make it visible to the student."
    ))
    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name, user_role=current_user.role.value,
        action="montessori_approve", entity_type="montessori_report", entity_id=r.id,
        description=f"Approved Montessori report {r.id}"
    ))
    db.commit()
    return {"message": "Approved. Use 'Publish' to make results visible to the student."}


# ── Admin: reject ───────────────────────────────────────────────────
@router.post("/{report_id}/reject")
def reject_report(report_id: int, body: dict, db: Session = Depends(get_db),
                   current_user: User = Depends(require_admin)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    if r.status == ResultStatus.locked:
        raise HTTPException(403, "Cannot reject a locked report")
    r.status = ResultStatus.rejected
    r.admin_note = body.get("reason") or body.get("comment") or ""
    db.add(Notification(
        user_id=r.entered_by, type="rejected", title="Montessori Report Rejected",
        message=f"{r.student.full_name if r.student else ''}: {r.admin_note}"
    ))
    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name, user_role=current_user.role.value,
        action="montessori_reject", entity_type="montessori_report", entity_id=r.id,
        description=f"Rejected Montessori report {r.id}: {r.admin_note}"
    ))
    db.commit()
    return {"message": "Rejected. Sub admin has been notified."}


# ── Admin: request correction ───────────────────────────────────────
@router.post("/{report_id}/correction")
def correction_report(report_id: int, body: dict, db: Session = Depends(get_db),
                       current_user: User = Depends(require_admin)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    if r.status == ResultStatus.locked:
        raise HTTPException(403, "Cannot request correction on a locked report")
    r.status = ResultStatus.correction_requested
    r.admin_note = body.get("reason") or body.get("comment") or ""
    db.add(Notification(
        user_id=r.entered_by, type="correction", title="Correction Requested",
        message=f"{r.student.full_name if r.student else ''}: {r.admin_note}"
    ))
    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name, user_role=current_user.role.value,
        action="montessori_correction", entity_type="montessori_report", entity_id=r.id,
        description=f"Correction requested on Montessori report {r.id}: {r.admin_note}"
    ))
    db.commit()
    return {"message": "Correction requested. Sub admin has been notified."}


# ── Staff: publish / lock a report so the student can see it ──────
@router.post("/{report_id}/publish")
def publish_report(report_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    if r.status == ResultStatus.locked:
        raise HTTPException(403, "Cannot publish a locked report")
    # Defensive recompute: guarantees the published text always matches the
    # ratings actually on file, even if some other path touched the record
    # without going through save_report's own recompute.
    r.class_teacher_report = _auto_class_teacher_report(r.ratings)
    r.proprietors_report = _auto_proprietors_report(r.ratings)
    r.status = ResultStatus.published
    r.approved_by = current_user.id
    r.approved_at = datetime.now(timezone.utc)
    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name, user_role=current_user.role.value,
        action="montessori_publish", entity_type="montessori_report", entity_id=r.id,
        description=f"Published Montessori report {r.id}"
    ))
    db.commit()
    return {"message": "Published", "id": r.id}


# ── Admin: lock / unlock ────────────────────────────────────────────
@router.post("/{report_id}/lock")
def lock_report(report_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    r.status = ResultStatus.locked
    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name, user_role=current_user.role.value,
        action="montessori_lock", entity_type="montessori_report", entity_id=r.id,
        description=f"Locked Montessori report {r.id}"
    ))
    db.commit()
    return {"message": "Report locked. Sub admin cannot edit it."}


@router.post("/{report_id}/unlock")
def unlock_report(report_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_admin)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    r.status = ResultStatus.approved
    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name, user_role=current_user.role.value,
        action="montessori_unlock", entity_type="montessori_report", entity_id=r.id,
        description=f"Unlocked Montessori report {r.id}"
    ))
    db.commit()
    return {"message": "Report unlocked."}


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
    _term_cache = {}
    return {"items": [_out(r, db, _term_cache) for r in items]}


@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(require_staff)):
    r = db.query(MontessoriReport).filter(MontessoriReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    return _out(r, db)


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
    _term_cache = {}
    return {"items": [_out(r, db, _term_cache) for r in items]}