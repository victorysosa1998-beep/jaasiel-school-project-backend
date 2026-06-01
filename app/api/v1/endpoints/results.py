"""
Results endpoint — complete workflow:
  Sub Admin: upload scores per subject (status=pending/draft)
             → submit ALL subjects for a class → status=submitted → admin sees it
  Admin:     review → approve (status=approved)
             → publish per class or all classes → status=published → students see it
  Admin:     lock → sub admins CANNOT edit locked/published results
  Results stored forever (transcripts always available)
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from app.db.base import get_db
from app.models.models import (Result, ResultBatch, ResultStatus, Class, Subject,
                                Session as AcSession, Term, Student, User, UserRole,
                                AuditLog, Notification)
from app.api.v1.deps import get_current_user, require_admin, require_staff
from app.utils.grading import calculate_grade

router = APIRouter()


# ── Pydantic ──────────────────────────────────────────────────
class ScoreItem(BaseModel):
    student_id:     int
    first_test:     Optional[float] = None   # out of 20 (optional)
    second_test:    Optional[float] = None   # out of 20 (optional)
    ca_score:       Optional[float] = None   # combined CA (or direct if no split)
    exam_score:     Optional[float] = None
    total_score:    Optional[float] = None
    teacher_comment:Optional[str]   = None
    attendance:     Optional[int]   = None   # days school opened
    days_present:   Optional[int]   = None   # days student was present
    days_absent:    Optional[int]   = None   # days student was absent

class UploadRequest(BaseModel):
    class_name:  Optional[str] = None
    class_id:    Optional[int] = None
    subject_id:  Optional[int] = None
    subject_name:Optional[str] = None
    session_id:  Optional[int] = None
    term_id:     Optional[int] = None
    source:      str = "manual"
    max_ca:      Optional[float] = None   # max CA marks (default 40 if split, else as uploaded)
    max_exam:    Optional[float] = None   # max exam marks (default 60)
    no_test:     bool = False             # True if teacher enters score directly (over 100, no CA split)
    scores:      List[ScoreItem]

class ActionRequest(BaseModel):
    reason:  Optional[str] = None
    comment: Optional[str] = None


# ── Serialiser ────────────────────────────────────────────────
def _batch_out(b: ResultBatch, include_results: bool = False) -> dict:
    results = b.results or []
    totals  = [r.total_score or 0 for r in results if r.total_score is not None]
    out = {
        "id":           b.id,
        "class_name":   b.class_.name if b.class_ else "—",
        "class_id":     b.class_id,
        "subject_name": b.subject.name if b.subject else "—",
        "subject_id":   b.subject_id,
        "session_name": b.session.session_name if b.session else "—",
        "session_id":   b.session_id,
        "term_name":    b.term.term_name if b.term else "—",
        "term_id":      b.term_id,
        "uploader":     b.uploader.full_name if b.uploader else "—",
        "uploader_id":  b.uploaded_by,
        "status":       b.status.value,
        "upload_type":  b.upload_type,
        "source":       b.upload_type,
        "has_issues":   bool(b.has_issues),
        "admin_note":   b.admin_note,
        "correction_comment": b.admin_note,
        "students":     len(results),
        "avg_score":    round(sum(totals)/len(totals), 1) if totals else None,
        "pass_count":   sum(1 for t in totals if t >= 40),
        "uploaded_at":  b.uploaded_at.isoformat() if b.uploaded_at else None,
        "approved_at":  b.approved_at.isoformat() if b.approved_at else None,
    }
    if include_results:
        out["scores"] = [{
            "student_id":    r.student_id,
            "student_name":  r.student.full_name if r.student else "—",
            "full_name":     r.student.full_name if r.student else "—",
            "student_id_no": r.student.student_id if r.student else "—",
            "first_test":  r.first_test,
            "second_test": r.second_test,
            "ca_score":    r.ca_score,
            "exam_score":  r.exam_score,
            "total_score": r.total_score,
            "grade":       r.grade,
            "remark":      r.remark,
            "position":    r.position,
            "teacher_comment": r.teacher_comment,
            "admin_comment":   r.admin_comment,
            "conduct_comment": r.conduct_comment if hasattr(r, "conduct_comment") else None,
            "attendance":      r.attendance,
            "days_present":    r.days_present,
            "days_absent":     r.days_absent,
            "status":      r.status.value,
        } for r in results]
    return out


# ─────────────────────────────────────────────────────────────
# SUB ADMIN — Upload scores for one subject (saves as draft)
# ─────────────────────────────────────────────────────────────
@router.post("/upload")
def upload_results(body: UploadRequest,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):

    # resolve class
    cls = None
    if body.class_id:
        cls = db.query(Class).filter(Class.id == body.class_id).first()
    elif body.class_name:
        cls = db.query(Class).filter(Class.name == body.class_name).first()
        if not cls:
            cls = Class(name=body.class_name); db.add(cls); db.flush()

    # resolve subject
    subject = None
    if body.subject_id:
        subject = db.query(Subject).filter(Subject.id == body.subject_id).first()
    elif body.subject_name:
        subject = db.query(Subject).filter(Subject.name == body.subject_name).first()
        if not subject:
            subject = Subject(name=body.subject_name); db.add(subject); db.flush()

    if not cls or not subject:
        raise HTTPException(400, "Class and subject are required")

    session = db.query(AcSession).filter(AcSession.id == body.session_id).first()
    term    = db.query(Term).filter(Term.id == body.term_id).first()
    if not session or not term:
        raise HTTPException(400, "Valid session and term are required")

    # ── LOCK GUARD: block edits if any batch for this class+term is locked/published ──
    locked = db.query(ResultBatch).filter(
        ResultBatch.class_id == cls.id,
        ResultBatch.term_id  == term.id,
        ResultBatch.status.in_([ResultStatus.locked, ResultStatus.published])
    ).first()
    if locked:
        raise HTTPException(403,
            f"Results for {cls.name} this term are LOCKED. Contact Super Admin to unlock.")

    # ── Block edits only if admin has approved or locked ──
    # Sub-admin CAN re-upload/edit while status is pending (draft) OR submitted
    already_final = db.query(ResultBatch).filter(
        ResultBatch.class_id   == cls.id,
        ResultBatch.subject_id == subject.id,
        ResultBatch.term_id    == term.id,
        ResultBatch.status.in_([
            ResultStatus.approved, ResultStatus.locked, ResultStatus.published
        ])
    ).first()
    if already_final:
        status_label = already_final.status.value
        raise HTTPException(403,
            f"{subject.name} results have been {status_label} by the admin "
            f"and can no longer be edited. Contact Super Admin if changes are needed.")

    # Delete any existing pending OR submitted batch so sub-admin can re-upload
    # (submitted batch is reset to pending so admin must re-approve)
    old_batch = db.query(ResultBatch).filter(
        ResultBatch.class_id   == cls.id,
        ResultBatch.subject_id == subject.id,
        ResultBatch.term_id    == term.id,
        ResultBatch.status.in_([ResultStatus.pending, ResultStatus.submitted]),
    ).first()
    if old_batch:
        was_submitted = old_batch.status == ResultStatus.submitted
        db.query(Result).filter(Result.batch_id == old_batch.id).delete()
        db.delete(old_batch)
        db.flush()
        if was_submitted:
            db.add(AuditLog(
                user_id=current_user.id, user_name=current_user.full_name,
                user_role=current_user.role.value, action="edit",
                entity_type="result_batch",
                description=f"Re-edited submitted results for {subject.name} — reset to draft"
            ))

    # Create batch (draft — not yet submitted to admin)
    batch = ResultBatch(
        uploaded_by=current_user.id, class_id=cls.id,
        subject_id=subject.id, session_id=session.id,
        term_id=term.id, upload_type=body.source,
        status=ResultStatus.pending,
    )
    db.add(batch); db.flush()

    created, skipped = 0, 0
    for sc in body.scores:
        student = db.query(Student).filter(Student.id == sc.student_id).first()
        if not student: skipped += 1; continue

        # Determine CA: if first_test/second_test provided, sum them; else use ca_score directly
        first_t  = sc.first_test  if sc.first_test  is not None else None
        second_t = sc.second_test if sc.second_test is not None else None

        if first_t is not None and second_t is not None:
            ca = first_t + second_t
        elif first_t is not None:
            ca = first_t
        elif sc.ca_score is not None:
            ca = sc.ca_score
        else:
            ca = 0

        exam  = sc.exam_score or 0

        # If no_test mode: teacher enters total directly (no CA breakdown)
        if body.no_test:
            total = sc.total_score if sc.total_score is not None else exam
            ca    = 0
        else:
            total = sc.total_score if sc.total_score is not None else (ca + exam)

        g, r  = calculate_grade(total)
        existing = db.query(Result).filter(
            Result.student_id == student.id,
            Result.subject_id == subject.id,
            Result.term_id    == term.id,
        ).first()
        if existing:
            existing.first_test=first_t; existing.second_test=second_t
            existing.ca_score=ca; existing.exam_score=exam; existing.total_score=total
            existing.grade=g; existing.remark=r; existing.batch_id=batch.id
            existing.status=ResultStatus.pending
            if sc.teacher_comment is not None: existing.teacher_comment=sc.teacher_comment
            if sc.attendance is not None: existing.attendance=sc.attendance
            if sc.days_present is not None: existing.days_present=sc.days_present
            if sc.days_absent  is not None: existing.days_absent=sc.days_absent
        else:
            db.add(Result(
                student_id=student.id, class_id=cls.id,
                subject_id=subject.id, session_id=session.id,
                term_id=term.id, batch_id=batch.id,
                first_test=first_t, second_test=second_t,
                ca_score=ca, exam_score=exam, total_score=total,
                grade=g, remark=r, status=ResultStatus.pending,
                teacher_comment=sc.teacher_comment,
                attendance=sc.attendance,
                days_present=sc.days_present,
                days_absent=sc.days_absent,
            ))
        created += 1

    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="upload",
                    entity_type="result_batch", entity_id=batch.id,
                    description=f"Saved draft: {cls.name} — {subject.name} ({created} students)"))
    db.commit()
    return {"message": f"Scores saved as draft ({created} students). "
                       "Upload all subjects then click 'Submit to Admin'.",
            "batch_id": batch.id, "created": created, "skipped": skipped}


# ─────────────────────────────────────────────────────────────
# SUB ADMIN — Submit ALL pending subjects for a class to admin
# ─────────────────────────────────────────────────────────────
@router.post("/submit-class")
def submit_class(body: dict,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_staff)):
    """
    Sub admin calls this after uploading ALL subjects for a class+term.
    Marks every pending batch for that class+term as 'submitted'.
    Admin is then notified to review.
    """
    class_id  = body.get("class_id")
    term_id   = body.get("term_id")
    session_id= body.get("session_id")
    if not class_id or not term_id:
        raise HTTPException(400, "class_id and term_id required")

    cls  = db.query(Class).filter(Class.id == class_id).first()
    term = db.query(Term).filter(Term.id == term_id).first()
    if not cls or not term:
        raise HTTPException(404, "Class or term not found")

    # LOCK GUARD
    locked = db.query(ResultBatch).filter(
        ResultBatch.class_id == class_id,
        ResultBatch.term_id  == term_id,
        ResultBatch.status.in_([ResultStatus.locked, ResultStatus.published])
    ).first()
    if locked:
        raise HTTPException(403, f"Results for {cls.name} are LOCKED.")

    pending_batches = db.query(ResultBatch).filter(
        ResultBatch.class_id    == class_id,
        ResultBatch.term_id     == term_id,
        ResultBatch.uploaded_by == current_user.id,
        ResultBatch.status      == ResultStatus.pending,
    ).all()

    if not pending_batches:
        raise HTTPException(400,
            "No pending (draft) results found for this class. "
            "Upload scores for at least one subject first.")

    count = len(pending_batches)
    for b in pending_batches:
        b.status = ResultStatus.submitted
        for r in b.results:
            r.status = ResultStatus.submitted

    # Notify all admins
    admins = db.query(User).filter(
        User.role.in_([UserRole.super_admin, UserRole.admin]),
        User.is_active == True
    ).all()
    for admin in admins:
        db.add(Notification(
            user_id=admin.id, type="upload",
            title="Results Ready for Review",
            message=f"{current_user.full_name} submitted {count} subject(s) "
                    f"for {cls.name} — {term.term_name}. Ready for your approval."
        ))

    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="submit",
                    entity_type="class_results",
                    description=f"Submitted {count} batches for {cls.name} — {term.term_name}"))
    db.commit()
    return {"message": f"{count} subject(s) submitted to admin for approval.",
            "submitted": count}


# ─────────────────────────────────────────────────────────────
# SUB ADMIN — Check upload status for a class+term
# ─────────────────────────────────────────────────────────────
@router.get("/class-status")
def class_upload_status(
    class_id:   int,
    term_id:    int,
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Returns which subjects have been uploaded (draft/submitted) for a class+term."""
    batches = db.query(ResultBatch).filter(
        ResultBatch.class_id   == class_id,
        ResultBatch.term_id    == term_id,
        ResultBatch.session_id == session_id,
    ).all()
    return {"batches": [_batch_out(b) for b in batches]}


# ─────────────────────────────────────────────────────────────
# BATCH LIST & DETAIL
# ─────────────────────────────────────────────────────────────
@router.get("/batches")
def list_batches(
    status:   Optional[str] = None,
    class_id: Optional[int] = None,
    term_id:  Optional[int] = None,
    page:     int = 1,
    per_page: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    q = db.query(ResultBatch)
    # Sub admins only see their own pending/submitted
    if current_user.role.value == "sub_admin":
        q = q.filter(ResultBatch.uploaded_by == current_user.id)
    if status:
        try: q = q.filter(ResultBatch.status == ResultStatus(status))
        except ValueError: pass
    if class_id: q = q.filter(ResultBatch.class_id == class_id)
    if term_id:  q = q.filter(ResultBatch.term_id  == term_id)
    total = q.count()
    items = q.order_by(ResultBatch.uploaded_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    return {"items": [_batch_out(b) for b in items], "total": total, "page": page}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int,
              db: Session = Depends(get_db),
              current_user: User = Depends(require_staff)):
    b = db.query(ResultBatch).filter(ResultBatch.id == batch_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    return _batch_out(b, include_results=True)


@router.get("/pending")
def pending_batches(page: int = 1, per_page: int = 10,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_staff)):
    # Admin sees submitted batches (not raw pending drafts)
    q = db.query(ResultBatch).filter(
        ResultBatch.status == ResultStatus.submitted
    )
    total = q.count()
    items = q.order_by(ResultBatch.uploaded_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    return {"items": [_batch_out(b) for b in items], "total": total}


# ─────────────────────────────────────────────────────────────
# ADMIN — Approve a single batch
# ─────────────────────────────────────────────────────────────
@router.post("/classes/{class_id}/approve")
def approve_class(
    class_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Approve ALL submitted batches for a class in one click."""
    term_id    = body.get("term_id")
    session_id = body.get("session_id")
    if not term_id:
        raise HTTPException(400, "term_id is required")

    batches = db.query(ResultBatch).filter(
        ResultBatch.class_id  == class_id,
        ResultBatch.term_id   == term_id,
        ResultBatch.status    == ResultStatus.submitted,
    )
    if session_id:
        batches = batches.filter(ResultBatch.session_id == session_id)
    batches = batches.all()

    if not batches:
        raise HTTPException(404, "No submitted batches found for this class and term.")

    for b in batches:
        b.status       = ResultStatus.approved
        b.reviewed_by  = current_user.id
        b.reviewed_at  = datetime.utcnow()
        db.add(AuditLog(
            user_id=current_user.id, user_name=current_user.full_name,
            user_role=current_user.role.value, action="approve",
            entity_type="result_batch", entity_id=b.id,
            description=f"Approved {b.subject.name if b.subject else b.subject_id} for class {class_id} (bulk class approve)"
        ))

    db.commit()
    return {"message": f"Approved {len(batches)} subject(s) for this class.", "approved": len(batches)}


@router.post("/batches/{batch_id}/approve")
def approve_batch(batch_id: int,
                  body: ActionRequest = ActionRequest(),
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_admin)):
    b = db.query(ResultBatch).filter(ResultBatch.id == batch_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    if b.status == ResultStatus.locked:
        raise HTTPException(403, "Cannot approve a locked batch")
    now = datetime.now(timezone.utc)
    b.status      = ResultStatus.approved
    b.approved_at = now
    b.approved_by = current_user.id
    for r in b.results:
        r.status      = ResultStatus.approved
        r.approved_at = now
        r.approved_by = current_user.id
    _recalculate_positions(b.class_id, b.term_id, db)
    db.add(Notification(user_id=b.uploaded_by, type="approved",
                        title="Results Approved",
                        message=f"{b.class_.name} — {b.subject.name} approved. "
                                "Awaiting publish."))
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="approve",
                    entity_type="result_batch", entity_id=batch_id,
                    description=f"Approved batch {batch_id}: {b.class_.name} {b.subject.name}"))
    db.commit()
    return {"message": "Batch approved. Use 'Publish' to make results visible to students."}


# ─────────────────────────────────────────────────────────────
# ADMIN — Reject a batch
# ─────────────────────────────────────────────────────────────
@router.post("/batches/{batch_id}/reject")
def reject_batch(batch_id: int, body: ActionRequest,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin)):
    b = db.query(ResultBatch).filter(ResultBatch.id == batch_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    if b.status == ResultStatus.locked:
        raise HTTPException(403, "Cannot reject a locked batch")
    b.status     = ResultStatus.rejected
    b.admin_note = body.reason or body.comment or ""
    for r in b.results:
        r.status = ResultStatus.rejected
    db.add(Notification(user_id=b.uploaded_by, type="rejected",
                        title="Results Rejected",
                        message=f"{b.class_.name} — {b.subject.name}: {b.admin_note}"))
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="reject",
                    entity_type="result_batch", entity_id=batch_id,
                    description=f"Rejected batch {batch_id}: {b.admin_note}"))
    db.commit()
    return {"message": "Batch rejected. Sub admin has been notified."}


# ─────────────────────────────────────────────────────────────
# ADMIN — Request correction
# ─────────────────────────────────────────────────────────────
@router.post("/batches/{batch_id}/correction")
def correction_batch(batch_id: int, body: ActionRequest,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin)):
    b = db.query(ResultBatch).filter(ResultBatch.id == batch_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    if b.status == ResultStatus.locked:
        raise HTTPException(403, "Cannot request correction on a locked batch")
    comment  = body.reason or body.comment or ""
    b.status     = ResultStatus.correction_requested
    b.admin_note = comment
    for r in b.results:
        r.status = ResultStatus.correction_requested
    db.add(Notification(user_id=b.uploaded_by, type="correction",
                        title="Correction Requested",
                        message=f"{b.class_.name} — {b.subject.name}: {comment}"))
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="correction",
                    entity_type="result_batch", entity_id=batch_id,
                    description=f"Correction requested batch {batch_id}: {comment}"))
    db.commit()
    return {"message": "Correction requested. Sub admin has been notified."}


# ─────────────────────────────────────────────────────────────
# ADMIN — Publish results (per class or all)
# Makes results visible to students
# ─────────────────────────────────────────────────────────────
@router.post("/publish")
def publish_results(body: dict,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin)):
    """
    Publish approved results.
    body = { class_id: int } — publishes one class
    body = { all: true, term_id: int, session_id: int } — publishes all classes
    """
    term_id    = body.get("term_id")
    session_id = body.get("session_id")
    class_id   = body.get("class_id")
    publish_all= body.get("all", False)

    if not term_id or not session_id:
        raise HTTPException(400, "term_id and session_id required")

    q = db.query(ResultBatch).filter(
        ResultBatch.term_id    == term_id,
        ResultBatch.session_id == session_id,
        ResultBatch.status     == ResultStatus.approved,
    )
    if not publish_all:
        if not class_id:
            raise HTTPException(400, "class_id required when not publishing all")
        q = q.filter(ResultBatch.class_id == class_id)

    batches = q.all()
    if not batches:
        raise HTTPException(400, "No approved batches found to publish. Approve results first.")

    now = datetime.now(timezone.utc)
    published_classes = set()
    for b in batches:
        b.status = ResultStatus.published
        for r in b.results:
            r.status = ResultStatus.published
        published_classes.add(b.class_id)

    # Recalculate positions for each published class
    for cid in published_classes:
        _recalculate_positions(cid, term_id, db)

    # Notify all students in affected classes
    for cid in published_classes:
        students = db.query(Student).filter(Student.class_id == cid, Student.is_active == True).all()
        cls_name = (db.query(Class).filter(Class.id == cid).first() or type('', (), {'name': '—'})()).name
        term = db.query(Term).filter(Term.id == term_id).first()
        for stu in students:
            db.add(Notification(student_id=stu.id, type="approved",
                                title="Your Results Are Available",
                                message=f"Your {term.term_name if term else ''} results for "
                                        f"{cls_name} have been published. Login to view."))

    desc = (f"Published ALL classes for term {term_id}" if publish_all
            else f"Published class_id={class_id} for term {term_id}")
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="publish",
                    entity_type="results",
                    description=desc))
    db.commit()
    return {"message": f"Results published for {len(published_classes)} class(es). "
                       "Students can now view their results.",
            "published_classes": len(published_classes),
            "published_batches": len(batches)}


# ─────────────────────────────────────────────────────────────
# ADMIN — Lock results (prevents ALL edits)
# ─────────────────────────────────────────────────────────────
@router.post("/{batch_id}/lock")
def lock_result(batch_id: int,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_admin)):
    b = db.query(ResultBatch).filter(ResultBatch.id == batch_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    b.status = ResultStatus.locked
    for r in b.results:
        r.status = ResultStatus.locked
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="lock",
                    entity_type="result_batch", entity_id=batch_id,
                    description=f"Locked batch {batch_id}: {b.class_.name} {b.subject.name}"))
    db.commit()
    return {"message": "Results locked. Sub admins cannot edit these results."}


@router.post("/{batch_id}/unlock")
def unlock_result(batch_id: int,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_admin)):
    b = db.query(ResultBatch).filter(ResultBatch.id == batch_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    b.status = ResultStatus.approved
    for r in b.results:
        r.status = ResultStatus.approved
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="unlock",
                    entity_type="result_batch", entity_id=batch_id,
                    description=f"Unlocked batch {batch_id}"))
    db.commit()
    return {"message": "Results unlocked."}


# ─────────────────────────────────────────────────────────────
# ADMIN — Overview: which classes have all subjects approved
# ─────────────────────────────────────────────────────────────
@router.get("/publish-overview")
def publish_overview(term_id: int, session_id: int,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin)):
    """
    Returns per-class summary: how many subjects submitted/approved/published.
    Used to drive the 'ready to publish' checklist on the approval page.
    """
    batches = db.query(ResultBatch).filter(
        ResultBatch.term_id    == term_id,
        ResultBatch.session_id == session_id,
    ).all()

    class_map = {}
    for b in batches:
        cid  = b.class_id
        cname= b.class_.name if b.class_ else "—"
        if cid not in class_map:
            class_map[cid] = {
                "class_id": cid, "class_name": cname,
                "submitted": 0, "approved": 0, "published": 0,
                "pending": 0, "rejected": 0, "correction": 0,
                "locked": 0, "total_batches": 0,
            }
        class_map[cid]["total_batches"] += 1
        s = b.status.value
        if s == "submitted":   class_map[cid]["submitted"] += 1
        elif s == "approved":  class_map[cid]["approved"]  += 1
        elif s == "published": class_map[cid]["published"] += 1
        elif s == "pending":   class_map[cid]["pending"]   += 1
        elif s == "rejected":  class_map[cid]["rejected"]  += 1
        elif s == "correction_requested": class_map[cid]["correction"] += 1
        elif s == "locked":    class_map[cid]["locked"]    += 1

    # Mark classes ready to publish (all batches approved, none pending/submitted/rejected)
    for v in class_map.values():
        v["ready_to_publish"] = (v["approved"] > 0 and
                                  v["pending"] == 0 and
                                  v["submitted"] == 0 and
                                  v["rejected"] == 0 and
                                  v["correction"] == 0 and
                                  v["published"] == 0)
        v["all_published"] = (v["published"] > 0 and
                               v["approved"] == 0 and
                               v["pending"] == 0 and
                               v["submitted"] == 0)

    return {"classes": list(class_map.values())}


# ─────────────────────────────────────────────────────────────
# TRANSCRIPT — all results for a student (any term range)
# ─────────────────────────────────────────────────────────────
@router.get("/transcript/{student_id}")
def get_transcript(student_id: int,
                   from_session_id: Optional[int] = None,
                   to_session_id:   Optional[int] = None,
                   from_term_id:    Optional[int] = None,
                   to_term_id:      Optional[int] = None,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student: raise HTTPException(404, "Student not found")

    q = db.query(Result).filter(
        Result.student_id == student_id,
        Result.status.in_([ResultStatus.approved, ResultStatus.published, ResultStatus.locked])
    )
    if from_session_id: q = q.filter(Result.session_id >= from_session_id)
    if to_session_id:   q = q.filter(Result.session_id <= to_session_id)
    if from_term_id:    q = q.filter(Result.term_id    >= from_term_id)
    if to_term_id:      q = q.filter(Result.term_id    <= to_term_id)

    results = q.order_by(Result.session_id, Result.term_id).all()

    # Group by session+term
    from collections import defaultdict, OrderedDict
    groups = OrderedDict()
    for r in results:
        key = (r.session_id, r.term_id)
        if key not in groups:
            groups[key] = {
                "session_id":   r.session_id,
                "session_name": r.session.session_name if r.session else "—",
                "term_id":      r.term_id,
                "term_name":    r.term.term_name if r.term else "—",
                "results":      [],
            }
        groups[key]["results"].append({
            "subject_name": r.subject.name if r.subject else "—",
            "first_test":  r.first_test,
            "second_test": r.second_test,
            "ca_score":    r.ca_score,
            "exam_score":  r.exam_score,
            "total_score": r.total_score,
            "grade":       r.grade,
            "remark":      r.remark,
            "position":    r.position,
            "teacher_comment": r.teacher_comment,
            "admin_comment":   r.admin_comment,
            "conduct_comment": r.conduct_comment if hasattr(r, "conduct_comment") else None,
            "attendance":      r.attendance,
        })

    # Compute term averages
    for g in groups.values():
        totals = [x["total_score"] or 0 for x in g["results"]]
        g["average"]    = round(sum(totals)/len(totals), 1) if totals else 0
        g["total_score"]= sum(totals)
        g["subjects"]   = len(totals)

    return {
        "student_id":   student.id,
        "full_name":    student.full_name,
        "student_no":   student.student_id,
        "class_name":   student.class_.name if student.class_ else "—",
        "date_of_birth":student.date_of_birth.isoformat() if student.date_of_birth else None,
        "gender":       student.gender.value if student.gender else None,
        "terms":        list(groups.values()),
        "total_terms":  len(groups),
        "overall_avg":  round(
            sum(g["average"] for g in groups.values()) / len(groups), 1
        ) if groups else 0,
    }


# ─────────────────────────────────────────────────────────────
# ADMIN/SUBADMIN — Add admin comment on a student result batch
# ─────────────────────────────────────────────────────────────
@router.post("/batches/{batch_id}/comment")
def add_admin_comment(batch_id: int, body: dict,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(require_staff)):
    """Sub admin or admin can add a comment on teacher remarks per student result."""
    student_id      = body.get("student_id")
    comment         = body.get("comment", "")
    conduct_comment = body.get("conduct_comment")   # optional conduct override
    b = db.query(ResultBatch).filter(ResultBatch.id == batch_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    if student_id:
        result = db.query(Result).filter(
            Result.batch_id == batch_id,
            Result.student_id == student_id
        ).first()
        if result:
            result.admin_comment = comment
            if conduct_comment is not None:
                result.conduct_comment = conduct_comment
    db.commit()
    return {"message": "Comment saved"}


# ─────────────────────────────────────────────────────────────
# ADMIN — Set conduct comment for a student per term
# Applies to ALL result rows for that student+term so it appears
# once on the report card regardless of number of subjects.
# ─────────────────────────────────────────────────────────────
@router.post("/student/{student_id}/conduct")
def set_conduct_comment(
    student_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Admin sets a conduct/behaviour comment for a student for a specific term."""
    term_id = body.get("term_id")
    comment = body.get("conduct_comment", "")
    if not term_id:
        raise HTTPException(400, "term_id is required")

    results = db.query(Result).filter(
        Result.student_id == student_id,
        Result.term_id    == term_id,
    ).all()
    if not results:
        raise HTTPException(404, "No results found for this student and term")

    for r in results:
        r.conduct_comment = comment

    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name,
        user_role=current_user.role.value, action="update",
        entity_type="conduct_comment", entity_id=student_id,
        description=f"Set conduct comment for student {student_id}, term {term_id}: {comment[:80]}"
    ))
    db.commit()
    return {"message": "Conduct comment saved"}


# ─────────────────────────────────────────────────────────────
# ADMIN — Set resumption date and next-term fees
# ─────────────────────────────────────────────────────────────
@router.post("/terms/{term_id}/publish-settings")
def set_publish_settings(term_id: int, body: dict,
                         db: Session = Depends(get_db),
                         current_user: User = Depends(require_admin)):
    """
    Set resumption date (for next term) and next-term school fees per class.
    body = { resumption_date: "YYYY-MM-DD", next_term_fee: {"JSS 1": 50000, ...} }
    """
    from app.models.models import Term as TermModel
    term = db.query(TermModel).filter(TermModel.id == term_id).first()
    if not term: raise HTTPException(404, "Term not found")
    if body.get("resumption_date"):
        try:
            from dateutil import parser
            term.resumption_date = parser.parse(body["resumption_date"])
        except Exception as e:
            raise HTTPException(400, f"Invalid resumption_date format: {e}")
    if body.get("next_term_fee"):
        # MERGE into existing fees instead of overwriting — prevents wiping
        # fees for classes not included in this save operation
        existing = term.next_term_fee or {}
        existing.update(body["next_term_fee"])
        term.next_term_fee = existing
    db.commit()
    return {"message": "Publish settings saved"}


@router.post("/terms/{term_id}/sub-admin-fees")
def sub_admin_set_fees(term_id: int, body: dict,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(require_staff)):
    """Sub admin submits next-term school fees per class before sending to admin."""
    from app.models.models import Term as TermModel
    term = db.query(TermModel).filter(TermModel.id == term_id).first()
    if not term: raise HTTPException(404, "Term not found")
    existing = term.next_term_fee or {}
    if body.get("next_term_fee"):
        existing.update(body["next_term_fee"])
        term.next_term_fee = existing
    db.commit()
    return {"message": "School fees submitted"}


# ─────────────────────────────────────────────────────────────
# GENERAL LIST
# ─────────────────────────────────────────────────────────────
@router.get("")
def list_results(page: int = 1, per_page: int = 20,
                 class_name: Optional[str] = None,
                 subject_id: Optional[int] = None,
                 session_id: Optional[int] = None,
                 term_id:    Optional[int] = None,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_staff)):
    q = db.query(Result)
    if class_name:
        cls = db.query(Class).filter(Class.name == class_name).first()
        if cls: q = q.filter(Result.class_id == cls.id)
    if subject_id: q = q.filter(Result.subject_id == subject_id)
    if session_id: q = q.filter(Result.session_id == session_id)
    if term_id:    q = q.filter(Result.term_id    == term_id)
    total = q.count()
    items = q.offset((page-1)*per_page).limit(per_page).all()
    return {"items": [{
        "id": r.id, "student_id": r.student_id,
        "student_name": r.student.full_name if r.student else "—",
        "subject_name": r.subject.name if r.subject else "—",
        "ca_score": r.ca_score, "exam_score": r.exam_score,
        "total_score": r.total_score, "grade": r.grade, "remark": r.remark,
        "status": r.status.value,
    } for r in items], "total": total}


# ─────────────────────────────────────────────────────────────
# SUBADMIN uploads list
# ─────────────────────────────────────────────────────────────
@router.get("/subadmin/uploads")
def subadmin_uploads(page: int = 1, per_page: int = 15,
                     status: Optional[str] = None,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(require_staff)):
    q = db.query(ResultBatch).filter(ResultBatch.uploaded_by == current_user.id)
    if status:
        try: q = q.filter(ResultBatch.status == ResultStatus(status))
        except ValueError: pass
    total = q.count()
    items = q.order_by(ResultBatch.uploaded_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    return {"items": [_batch_out(b) for b in items], "total": total}


# ─────────────────────────────────────────────────────────────
# HELPER — recalculate class positions
# ─────────────────────────────────────────────────────────────
def _recalculate_positions(class_id: int, term_id: int, db: Session):
    """
    Recalculate class positions based on student averages.

    Position rules:
    - Creche, Nursery, Primary, Basic, JSS1–JSS3 → position calculated & stored.
    - SS1, SS2, SS3 → position stays NULL (not displayed on report card).
    """
    import re as _re
    from collections import defaultdict

    # Determine if this class should have positions
    cls_obj = db.query(Class).filter(Class.id == class_id).first()
    cls_name = (cls_obj.name or "").strip().upper() if cls_obj else ""
    is_senior_secondary = bool(_re.match(r"^SS\s*[123]$", cls_name))

    results = db.query(Result).filter(
        Result.class_id == class_id,
        Result.term_id  == term_id,
        Result.status.in_([ResultStatus.approved, ResultStatus.published, ResultStatus.locked])
    ).all()

    if is_senior_secondary:
        # SS1–SS3: clear any previously stored position — must be blank
        for r in results:
            r.position = None
        return

    # All other classes: rank by average (Jaasiel formula: total / scored subjects)
    from app.utils.grading import compute_subject_total
    student_totals = defaultdict(list)
    for r in results:
        t = compute_subject_total(r.first_test, r.second_test, r.ca_score, r.exam_score, r.total_score)
        if t is not None:
            student_totals[r.student_id].append(t)

    # Average = sum of scored subject totals / number of scored subjects
    avgs = {
        sid: sum(ts) / len(ts)
        for sid, ts in student_totals.items()
        if ts
    }
    ranked = sorted(avgs.keys(), key=lambda s: avgs[s], reverse=True)
    for pos, sid in enumerate(ranked, 1):
        for r in results:
            if r.student_id == sid:
                r.position = pos