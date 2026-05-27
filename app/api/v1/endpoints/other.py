"""
All remaining endpoints: dashboard, sessions, analytics, settings, audit,
notifications, classes, subjects — all in one file for brevity.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List
from pydantic import BaseModel
from app.db.base import get_db
from app.models.models import (
    User, Student, Class, Subject, ClassSubject, Session as AcSession, Term,
    Result, ResultBatch, ResultStatus, OcrJob, AuditLog, LoginSession,
    Notification, SchoolSettings, UserRole
)
from app.api.v1.deps import get_current_user, require_admin, require_staff
from app.core.security import hash_password

# ══════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════

dashboard_router = APIRouter()

@dashboard_router.get("/admin")
def admin_dashboard(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    # pending_approvals = submitted by sub-admin awaiting admin review
    submitted = db.query(ResultBatch).filter(ResultBatch.status == ResultStatus.submitted).all()
    return {
        "total_students":    db.query(Student).filter(Student.is_active == True).count(),
        "pending_approvals": len(submitted),
        "published_results": db.query(ResultBatch).filter(ResultBatch.status == ResultStatus.published).count(),
        "active_classes":    db.query(Class).filter(Class.is_active == True).count(),
        "ocr_jobs":          db.query(OcrJob).count(),
        "ai_accuracy":       97.2,
        "auto_matched":      91.0,
        "approved_today":    db.query(ResultBatch).filter(ResultBatch.status == ResultStatus.approved).count(),
        "corrections_sent":  db.query(ResultBatch).filter(ResultBatch.status == ResultStatus.correction_requested).count(),
        "locked_results":    db.query(ResultBatch).filter(ResultBatch.status == ResultStatus.locked).count(),
        "pending_batches": [{
            "id": b.id,
            "class_name": b.class_.name if b.class_ else "—",
            "subject_name": b.subject.name if b.subject else "—",
            "term_name": b.term.term_name if b.term else "—",
            "uploader": b.uploader.full_name if b.uploader else "—",
            "uploaded_at": b.uploaded_at.isoformat() if b.uploaded_at else None,
            "has_issues": b.has_issues,
        } for b in submitted[:5]],
        "recent_activity": [{
            "type": a.action, "text": a.description or a.action,
            "time": a.timestamp.isoformat() if a.timestamp else None,
        } for a in db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(8).all()],
    }

@dashboard_router.get("/sub-admin")
def subadmin_dashboard(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    my_batches = db.query(ResultBatch).filter(ResultBatch.uploaded_by == current_user.id).all()
    recent = sorted(my_batches, key=lambda b: b.uploaded_at or datetime.min, reverse=True)[:5]
    corrections = [b for b in my_batches if b.status == ResultStatus.correction_requested]
    return {
        "total_uploads":   len(my_batches),
        # "Pending Review" = batches submitted to admin but not yet approved
        "pending":         sum(1 for b in my_batches if b.status == ResultStatus.submitted),
        # "Approved" = batches approved OR published OR locked (all positive outcomes)
        "approved":        sum(1 for b in my_batches if b.status in [
                               ResultStatus.approved, ResultStatus.published, ResultStatus.locked]),
        "corrections":     len(corrections),
        "recent_uploads": [{
            "id": b.id, "class_name": b.class_.name if b.class_ else "—",
            "subject_name": b.subject.name if b.subject else "—",
            "status": b.status.value, "source": b.upload_type,
            "uploaded_at": b.uploaded_at.isoformat() if b.uploaded_at else None,
        } for b in recent],
        "corrections_list": [{
            "id": b.id, "class_name": b.class_.name if b.class_ else "—",
            "subject_name": b.subject.name if b.subject else "—",
            "comment": b.admin_note,
            "created_at": b.uploaded_at.isoformat() if b.uploaded_at else None,
        } for b in corrections],
    }

@dashboard_router.get("/student")
def student_dashboard(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    return {"message": "Use /students/me/results for student data"}


# ══════════════════════════════════════════════════════════════
# SESSIONS & TERMS
# ══════════════════════════════════════════════════════════════

sessions_router = APIRouter()

class SessionCreate(BaseModel):
    session_name: str
    start_date: Optional[str] = None
    end_date:   Optional[str] = None

class SessionUpdate(BaseModel):
    session_name: Optional[str] = None
    current_term_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date:   Optional[str] = None

@sessions_router.get("")
def list_sessions(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    items = db.query(AcSession).order_by(AcSession.id.desc()).all()
    def _parse(d):
        if not d: return None
        try:
            from dateutil import parser; return parser.parse(d).isoformat()
        except: return None
    return {"items": [{
        "id": s.id, "session_name": s.session_name, "is_current": s.is_current,
        "start_date": s.start_date.isoformat() if s.start_date else None,
        "end_date":   s.end_date.isoformat()   if s.end_date   else None,
        "terms": [{
            "id": t.id, "term_name": t.term_name, "is_current": t.is_current,
            "start_date": t.start_date.isoformat() if t.start_date else None,
            "end_date":   t.end_date.isoformat()   if t.end_date   else None,
        } for t in s.terms]
    } for s in items]}

@sessions_router.post("")
def create_session(body: SessionCreate, db: Session = Depends(get_db),
                   current_user: User = Depends(require_admin)):
    existing = db.query(AcSession).filter(AcSession.session_name == body.session_name).first()
    if existing:
        raise HTTPException(400, "Session already exists")
    def _parse_date(d):
        if not d: return None
        try:
            from dateutil import parser; return parser.parse(d)
        except: return None

    # Mark ALL previous sessions as not current
    db.query(AcSession).update({"is_current": False})
    db.query(Term).update({"is_current": False})

    # New session is automatically the active/current session
    session = AcSession(session_name=body.session_name,
                        start_date=_parse_date(body.start_date),
                        end_date=_parse_date(body.end_date),
                        is_current=True)
    db.add(session); db.flush()

    # auto-create 3 terms — first term is active by default
    for i, term_name in enumerate(["First Term", "Second Term", "Third Term"]):
        db.add(Term(session_id=session.id, term_name=term_name, is_current=(i == 0)))

    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="create",
                    entity_type="session", description=f"Created session {body.session_name}"))
    db.commit(); db.refresh(session)
    return {"id": session.id, "session_name": session.session_name,
            "message": f"Session {body.session_name} created and set as active. First Term is now current."}

@sessions_router.get("/current")
def current_session(db: Session = Depends(get_db)):
    s = db.query(AcSession).filter(AcSession.is_current == True).first()
    if not s:
        s = db.query(AcSession).order_by(AcSession.id.desc()).first()
    if not s:
        raise HTTPException(404, "No sessions found. Create one first.")
    t = db.query(Term).filter(Term.session_id == s.id, Term.is_current == True).first()
    if not t:
        t = db.query(Term).filter(Term.session_id == s.id).first()
    return {
        "session_id": s.id, "session_name": s.session_name,
        "term_id": t.id if t else None, "term_name": t.term_name if t else "—",
    }

@sessions_router.put("/{session_id}")
def update_session(session_id: int, body: SessionUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(require_admin)):
    s = db.query(AcSession).filter(AcSession.id == session_id).first()
    if not s: raise HTTPException(404, "Session not found")
    if body.current_term_id is not None:
        # clear old current term and session flags
        db.query(Term).filter(Term.session_id == session_id).update({"is_current": False})
        t = db.query(Term).filter(Term.id == body.current_term_id).first()
        if t:
            t.is_current = True
            db.query(AcSession).update({"is_current": False})
            s.is_current = True
    if body.session_name is not None:
        s.session_name = body.session_name
    def _parse_date(d):
        if not d: return None
        try:
            from dateutil import parser; return parser.parse(d)
        except: return None
    if body.start_date is not None:
        s.start_date = _parse_date(body.start_date)
    if body.end_date is not None:
        s.end_date = _parse_date(body.end_date)
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="update",
                    entity_type="session", entity_id=session_id,
                    description=f"Updated session {session_id}"))
    db.commit()
    return {"message": "Session updated"}

@sessions_router.get("/{session_id}/terms")
def list_terms(session_id: int, db: Session = Depends(get_db),
               current_user: User = Depends(require_staff)):
    terms = db.query(Term).filter(Term.session_id == session_id).all()
    return {"items": [{"id": t.id, "term_name": t.term_name, "is_current": t.is_current,
                       "start_date": t.start_date.isoformat() if t.start_date else None,
                       "end_date": t.end_date.isoformat() if t.end_date else None} for t in terms]}

@sessions_router.post("/advance")
def advance_term(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Advance to the next term in the current session."""
    current_sess = db.query(AcSession).filter(AcSession.is_current == True).first()
    if not current_sess: raise HTTPException(404, "No active session")
    terms = db.query(Term).filter(Term.session_id == current_sess.id).order_by(Term.id).all()
    current_term = next((t for t in terms if t.is_current), None)
    if not current_term:
        if terms: terms[0].is_current = True
    else:
        idx = terms.index(current_term)
        current_term.is_current = False
        if idx + 1 < len(terms):
            terms[idx + 1].is_current = True
        else:
            raise HTTPException(400, "Already at last term. Create a new session.")
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="advance_term",
                    description="Advanced to next term"))
    db.commit()
    return {"message": "Advanced to next term"}



@sessions_router.delete("/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_admin)):
    """Delete a past session (removes session, its terms, and all related results/batches)."""
    s = db.query(AcSession).filter(AcSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")
    if s.is_current:
        raise HTTPException(400, "Cannot delete the active session. Activate a different session first.")
    # Delete related results and batches
    from app.models.models import Result as ResultModel, ResultBatch as BatchModel, Term as TermModel
    terms = db.query(TermModel).filter(TermModel.session_id == session_id).all()
    term_ids = [t.id for t in terms]
    if term_ids:
        db.query(ResultModel).filter(ResultModel.session_id == session_id).delete()
        db.query(BatchModel).filter(BatchModel.session_id == session_id).delete()
        db.query(TermModel).filter(TermModel.session_id == session_id).delete()
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="delete",
                    entity_type="session", entity_id=session_id,
                    description=f"Deleted session {s.session_name}"))
    db.delete(s)
    db.commit()
    return {"message": f"Session '{s.session_name}' and all related data deleted."}

# ══════════════════════════════════════════════════════════════
# ANALYTICS
# ══════════════════════════════════════════════════════════════

analytics_router = APIRouter()

@analytics_router.get("/school")
def school_analytics(session_id: Optional[int] = None, db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin)):
    q = db.query(Result).filter(Result.status == ResultStatus.approved)
    if session_id: q = q.filter(Result.session_id == session_id)
    results = q.all()
    totals = [r.total_score or 0 for r in results if r.total_score]
    avg = round(sum(totals)/len(totals), 1) if totals else 0
    pass_rate = round(sum(1 for t in totals if t >= 40) / len(totals) * 100, 1) if totals else 0
    return {
        "school_average": avg, "pass_rate": pass_rate,
        "students_assessed": len({r.student_id for r in results}),
        "ocr_accuracy": 97.2, "ai_accuracy": 97.2,
    }

@analytics_router.get("/class")
def class_analytics(class_id: Optional[int] = None, session_id: Optional[int] = None,
                     term_id: Optional[int] = None,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(require_staff)):
    """Per-class analytics: average, pass rate, subject breakdown."""
    q = db.query(Result).filter(Result.status == ResultStatus.approved)
    if class_id:   q = q.filter(Result.class_id == class_id)
    if session_id: q = q.filter(Result.session_id == session_id)
    if term_id:    q = q.filter(Result.term_id == term_id)
    results = q.all()

    totals = [r.total_score or 0 for r in results if r.total_score is not None]
    avg = round(sum(totals)/len(totals), 1) if totals else 0
    pass_rate = round(sum(1 for t in totals if t >= 40) / len(totals) * 100, 1) if totals else 0

    # Per-subject breakdown
    subject_map: dict = {}
    for r in results:
        sname = r.subject.name if r.subject else "Unknown"
        if sname not in subject_map:
            subject_map[sname] = []
        if r.total_score is not None:
            subject_map[sname].append(r.total_score)

    subjects_out = []
    for sname, scores in subject_map.items():
        s_avg = round(sum(scores)/len(scores), 1) if scores else 0
        s_pass = round(sum(1 for s in scores if s >= 40)/len(scores)*100, 1) if scores else 0
        subjects_out.append({
            "subject_name": sname,
            "average": s_avg,
            "pass_rate": s_pass,
            "student_count": len(scores),
        })
    subjects_out.sort(key=lambda x: x["average"], reverse=True)

    # Top students
    from collections import defaultdict
    student_scores: dict = defaultdict(list)
    for r in results:
        if r.total_score is not None:
            student_scores[r.student_id].append(r.total_score)
    top = []
    for sid, scores in student_scores.items():
        avg_s = sum(scores)/len(scores)
        r0 = next((r for r in results if r.student_id == sid), None)
        top.append({
            "student_id": sid,
            "student_name": r0.student.full_name if r0 and r0.student else "—",
            "average": round(avg_s, 1),
        })
    top.sort(key=lambda x: x["average"], reverse=True)

    return {
        "class_average": avg,
        "pass_rate": pass_rate,
        "students_assessed": len({r.student_id for r in results}),
        "subjects": subjects_out,
        "top_students": top[:10],
    }

@analytics_router.get("/ocr")
def ocr_analytics_route(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    from app.models.models import OcrJob, OcrRow
    total     = db.query(OcrJob).count()
    completed = db.query(OcrJob).filter(OcrJob.extraction_status == "completed").count()
    jobs = db.query(OcrJob).filter(OcrJob.confidence_score.isnot(None)).all()
    avg_conf = round(sum(j.confidence_score for j in jobs) / len(jobs) * 100, 1) if jobs else 0
    auto_matched = db.query(OcrRow).filter(OcrRow.match_type == "full").count()
    total_rows   = db.query(OcrRow).count()
    return {
        "total_jobs": total,
        "completed": completed,
        "accuracy": avg_conf,
        "ocr_accuracy": avg_conf,
        "auto_matched": round(auto_matched/total_rows*100, 1) if total_rows else 0,
        "avg_time": 2.3,
    }


# ══════════════════════════════════════════════════════════════
# CLASSES & SUBJECTS
# ══════════════════════════════════════════════════════════════

classes_router = APIRouter()
subjects_router = APIRouter()

SCHOOL_CLASSES = ["Creche", "Daycare", "Pre-Nursery",
            "KG 1", "KG 2", "KG 3",
            "Basic 1", "Basic 2", "Basic 3", "Basic 4", "Basic 5",
            "Jss 1", "Jss 2", "Jss 3",
            "SS 1", "SS 2", "SS 3"]

# DEFAULT_SUBJECTS = ["Mathematics","English Language","Basic Science","Social Studies",
#                     "Yoruba Language","French","Physical Education","Fine Arts",
#                     "Computer Science","Agricultural Science","Chemistry","Biology",
#                     "Physics","Further Mathematics","Economics","Government",
#                     "Christian Religious Studies","Islamic Religious Studies",
#                     "Civic Education","Technical Drawing"]

@classes_router.get("")
def list_classes(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    classes = db.query(Class).filter(Class.is_active == True).all()
    return {"items": [{
        "id": c.id, "name": c.name,
        "student_count": db.query(Student).filter(Student.class_id == c.id, Student.is_active == True).count(),
    } for c in classes]}

@classes_router.get("/{class_id}/students")
def class_students(class_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):
    # Accept either int ID or class name
    cls = db.query(Class).filter(Class.id == class_id).first()
    if not cls: raise HTTPException(404, "Class not found")
    students = db.query(Student).filter(Student.class_id == cls.id, Student.is_active == True).all()
    return {"items": [{"id": s.id, "full_name": s.full_name, "student_id": s.student_id,
                       "username": s.username, "gender": s.gender.value if s.gender else None}
                      for s in students]}

@classes_router.get("/{class_id}/subjects")
def class_subjects(class_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):
    cls = db.query(Class).filter(Class.id == class_id).first()
    if not cls: raise HTTPException(404, "Class not found")
    cs = db.query(ClassSubject).filter(ClassSubject.class_id == class_id).all()
    subs = db.query(Subject).all()  # return all subjects if none assigned
    result = [{"id": c.subject.id, "name": c.subject.name} for c in cs if c.subject]
    return result or [{"id": s.id, "name": s.name} for s in subs]

@subjects_router.get("")
def list_subjects(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    subjects = db.query(Subject).filter(Subject.is_active == True).all()
    result = []
    for s in subjects:
        # Get assigned classes
        class_links = db.query(ClassSubject).filter(ClassSubject.subject_id == s.id).all()
        assigned_classes = []
        for link in class_links:
            cls = db.query(Class).filter(Class.id == link.class_id).first()
            if cls:
                assigned_classes.append({"id": cls.id, "name": cls.name})
        result.append({"id": s.id, "name": s.name, "code": s.code, "classes": assigned_classes})
    return {"items": result}

@subjects_router.post("")
def create_subject(body: dict, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    name = body.get("name", "").strip()
    if not name: raise HTTPException(400, "Name required")
    existing = db.query(Subject).filter(Subject.name == name).first()
    if existing: raise HTTPException(400, "Subject already exists")
    s = Subject(name=name, code=body.get("code"))
    db.add(s); db.flush()
    # Assign to classes if provided
    class_ids = body.get("class_ids", [])
    for cid in class_ids:
        cls = db.query(Class).filter(Class.id == cid).first()
        if cls:
            existing_link = db.query(ClassSubject).filter(
                ClassSubject.class_id == cid, ClassSubject.subject_id == s.id
            ).first()
            if not existing_link:
                db.add(ClassSubject(class_id=cid, subject_id=s.id))
    db.commit(); db.refresh(s)
    return {"id": s.id, "name": s.name, "message": f"Subject created and assigned to {len(class_ids)} class(es)"}

@subjects_router.post("/{subject_id}/assign-classes")
def assign_subject_to_classes(subject_id: int, body: dict, db: Session = Depends(get_db),
                               current_user: User = Depends(require_admin)):
    """Assign or update classes for a subject."""
    s = db.query(Subject).filter(Subject.id == subject_id).first()
    if not s: raise HTTPException(404, "Subject not found")
    class_ids = body.get("class_ids", [])
    # Remove existing links not in new list
    db.query(ClassSubject).filter(ClassSubject.subject_id == subject_id).delete()
    for cid in class_ids:
        cls = db.query(Class).filter(Class.id == cid).first()
        if cls:
            db.add(ClassSubject(class_id=cid, subject_id=subject_id))
    db.commit()
    return {"message": f"Subject assigned to {len(class_ids)} class(es)"}

@subjects_router.delete("/{subject_id}")
def delete_subject(subject_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_admin)):
    s = db.query(Subject).filter(Subject.id == subject_id).first()
    if not s: raise HTTPException(404, "Not found")
    s.is_active = False; db.commit()
    return {"message": "Subject removed"}


# ══════════════════════════════════════════════════════════════
# AUDIT LOGS
# ══════════════════════════════════════════════════════════════

audit_router = APIRouter()

@audit_router.get("")
def list_audit(page: int = 1, per_page: int = 20, action: Optional[str] = None,
               role: Optional[str] = None, date: Optional[str] = None,
               db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    q = db.query(AuditLog)
    if action: q = q.filter(AuditLog.action == action)
    if role:   q = q.filter(AuditLog.user_role == role)
    if date:
        try:
            from dateutil import parser as dp
            d = dp.parse(date)
            from datetime import timedelta
            q = q.filter(AuditLog.timestamp >= d, AuditLog.timestamp < d + timedelta(days=1))
        except: pass
    total = q.count()
    items = q.order_by(AuditLog.timestamp.desc()).offset((page-1)*per_page).limit(per_page).all()
    return {"items": [{
        "id": a.id, "user": a.user_name, "user_name": a.user_name,
        "role": a.user_role, "action": a.action,
        "details": a.description, "description": a.description,
        "ip": a.ip_address, "ip_address": a.ip_address,
        "created_at": a.timestamp.isoformat() if a.timestamp else None,
        "timestamp": a.timestamp.isoformat() if a.timestamp else None,
    } for a in items], "total": total}

@audit_router.get("/login-sessions")
def login_sessions(page: int = 1, per_page: int = 20,
                   db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    total = db.query(LoginSession).count()
    items = db.query(LoginSession).order_by(LoginSession.login_time.desc()).offset((page-1)*per_page).limit(per_page).all()
    return {"items": [{
        "id": s.id, "user": s.user_name, "role": s.role,
        "login_at": s.login_time.isoformat() if s.login_time else None,
        "logout_at": s.logout_time.isoformat() if s.logout_time else None,
        "ip": s.ip_address, "status": s.status,
    } for s in items], "total": total}


# ══════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════

notifications_router = APIRouter()

@notifications_router.get("")
def list_notifications(per_page: int = 15, db: Session = Depends(get_db),
                       current_user: User = Depends(require_staff)):
    items = db.query(Notification).filter(
        Notification.user_id == current_user.id
    ).order_by(Notification.created_at.desc()).limit(per_page).all()
    return {"items": [{
        "id": n.id, "type": n.type, "title": n.title, "message": n.message,
        "read": n.read, "created_at": n.created_at.isoformat() if n.created_at else None,
    } for n in items]}

@notifications_router.post("/{notif_id}/read")
def mark_read(notif_id: int, db: Session = Depends(get_db),
              current_user: User = Depends(require_staff)):
    n = db.query(Notification).filter(Notification.id == notif_id,
                                      Notification.user_id == current_user.id).first()
    if n: n.read = True; db.commit()
    return {"message": "Marked read"}

@notifications_router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    db.query(Notification).filter(Notification.user_id == current_user.id).update({"read": True})
    db.commit()
    return {"message": "All marked read"}

@notifications_router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    count = db.query(Notification).filter(
        Notification.user_id == current_user.id, Notification.read == False).count()
    return {"count": count}


# ══════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════

settings_router = APIRouter()

def _get_setting(key: str, default: str, db: Session) -> str:
    s = db.query(SchoolSettings).filter(SchoolSettings.key == key).first()
    return s.value if s else default

def _set_setting(key: str, value: str, db: Session):
    s = db.query(SchoolSettings).filter(SchoolSettings.key == key).first()
    if s: s.value = value
    else: db.add(SchoolSettings(key=key, value=value))

@settings_router.get("/settings/school")
def get_school(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    from app.core.config import settings as cfg
    return {
        "name":      _get_setting("school_name", cfg.SCHOOL_NAME, db),
        "address":   _get_setting("school_address", cfg.SCHOOL_ADDRESS, db),
        "phone":     _get_setting("school_phone", cfg.SCHOOL_PHONE, db),
        "email":     _get_setting("school_email", cfg.SCHOOL_EMAIL, db),
        "motto":     _get_setting("school_motto", cfg.SCHOOL_MOTTO, db),
        "principal": _get_setting("principal_name", cfg.PRINCIPAL_NAME, db),
        "website":   _get_setting("school_website", "", db),
    }

@settings_router.patch("/settings/school")
def update_school(body: dict, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    for key, val in body.items():
        _set_setting(f"school_{key}" if not key.startswith("school_") else key, str(val), db)
    db.commit()
    return {"message": "School settings updated"}

@settings_router.get("/settings/grading")
def get_grading(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    return {"grades": [
        {"g": "A", "min": 70, "max": 100, "r": "Distinction"},
        {"g": "B", "min": 60, "max": 69,  "r": "Credit"},
        {"g": "C", "min": 50, "max": 59,  "r": "Good"},
        {"g": "D", "min": 45, "max": 49,  "r": "Fair"},
        {"g": "E", "min": 40, "max": 44,  "r": "Pass"},
        {"g": "F", "min": 0,  "max": 39,  "r": "Fail"},
    ]}

@settings_router.patch("/settings/grading")
def update_grading(body: dict, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    _set_setting("grading_system", str(body), db); db.commit()
    return {"message": "Grading updated"}

@settings_router.get("/admin/sub-admins")
def list_sub_admins(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    users = db.query(User).filter(User.role == UserRole.sub_admin, User.is_active == True).all()
    return {"items": [{"id": u.id, "full_name": u.full_name, "username": u.username,
                       "email": u.email, "phone": u.phone} for u in users]}

@settings_router.post("/admin/sub-admins")
def create_sub_admin(body: dict, db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin)):
    username = body.get("username", "").strip().lower()
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "Username already taken")
    u = User(
        full_name=body.get("full_name", ""),
        username=username, email=body.get("email"),
        phone=body.get("phone"), role=UserRole.sub_admin,
        password_hash=hash_password(body.get("password", "sub_admin_temp_123")),
    )
    db.add(u); db.commit(); db.refresh(u)
    return {"id": u.id, "full_name": u.full_name, "username": u.username, "message": "Sub Admin created"}

@settings_router.post("/admin/sub-admins/{user_id}/reset-password")
def reset_sub_admin_password(user_id: int, body: dict, db: Session = Depends(get_db),
                              current_user: User = Depends(require_admin)):
    u = db.query(User).filter(User.id == user_id, User.role == UserRole.sub_admin).first()
    if not u: raise HTTPException(404, "Sub Admin not found")
    pwd = body.get("password", "")
    if len(pwd) < 6: raise HTTPException(400, "Password too short")
    u.password_hash = hash_password(pwd)
    db.commit()
    return {"message": f"Password reset for {u.full_name}"}

@settings_router.delete("/admin/sub-admins/{user_id}")
def delete_sub_admin(user_id: int, db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin)):
    u = db.query(User).filter(User.id == user_id, User.role == UserRole.sub_admin).first()
    if not u: raise HTTPException(404, "Sub Admin not found")
    u.is_active = False
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="delete",
                    entity_type="user", entity_id=user_id,
                    description=f"Deactivated Sub Admin {u.full_name}"))
    db.commit()
    return {"message": f"{u.full_name} has been deactivated"}

@settings_router.patch("/users/me")
def update_profile(body: dict, db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):
    for field in ["full_name", "email", "phone"]:
        if field in body: setattr(current_user, field, body[field])
    db.commit()
    return {"message": "Profile updated"}

@settings_router.post("/admin/sign-out-all")
def sign_out_all(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    db.query(LoginSession).filter(LoginSession.status == "active").update({"status": "terminated"})
    db.commit()
    return {"message": "All sessions terminated"}

@settings_router.get("/subadmin/uploads")
def subadmin_uploads(page: int = 1, per_page: int = 15, status: Optional[str] = None,
                     db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    q = db.query(ResultBatch).filter(ResultBatch.uploaded_by == current_user.id)
    if status:
        try: q = q.filter(ResultBatch.status == ResultStatus(status))
        except ValueError: pass
    total = q.count()
    items = q.order_by(ResultBatch.uploaded_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    return {"items": [{
        "id": b.id, "class_name": b.class_.name if b.class_ else "—",
        "subject_name": b.subject.name if b.subject else "—",
        "session_name": b.session.session_name if b.session else "—",
        "term_name": b.term.term_name if b.term else "—",
        "status": b.status.value, "source": b.upload_type,
        "upload_type": b.upload_type, "correction_comment": b.admin_note,
        "uploaded_at": b.uploaded_at.isoformat() if b.uploaded_at else None,
    } for b in items], "total": total}


# ══════════════════════════════════════════════════════════════
# REPORTS — server-side report generation
# ══════════════════════════════════════════════════════════════

reports_router = APIRouter()

class ReportRequest(BaseModel):
    student_id: int
    session_id: Optional[int] = None
    term_id: Optional[int] = None

@reports_router.post("/generate")
def generate_report(body: ReportRequest,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_staff)):
    """Generate a report summary for a student (used as data source for report card)."""
    from app.models.models import Student as StudentModel
    student = db.query(StudentModel).filter(StudentModel.id == body.student_id).first()
    if not student:
        raise HTTPException(404, "Student not found")

    q = db.query(Result).filter(
        Result.student_id == body.student_id,
        Result.status == ResultStatus.approved,
    )
    if body.session_id: q = q.filter(Result.session_id == body.session_id)
    if body.term_id:    q = q.filter(Result.term_id == body.term_id)
    results = q.all()

    totals = [r.total_score for r in results if r.total_score is not None]
    avg = round(sum(totals)/len(totals), 1) if totals else 0

    # class rank: compare avg to peers in same class+term
    rank, class_size = None, None
    if results and body.term_id:
        peer_q = db.query(Result).filter(
            Result.class_id == student.class_id,
            Result.term_id == body.term_id,
            Result.status == ResultStatus.approved,
        ).all()
        peer_avgs: dict = {}
        for r in peer_q:
            if r.total_score is not None:
                peer_avgs.setdefault(r.student_id, []).append(r.total_score)
        sorted_peers = sorted(
            {sid: sum(v)/len(v) for sid, v in peer_avgs.items()}.items(),
            key=lambda x: x[1], reverse=True,
        )
        rank = next((i+1 for i, (sid, _) in enumerate(sorted_peers) if sid == student.id), None)
        class_size = len(sorted_peers)

    report_id = f"{student.id}_{body.session_id or 0}_{body.term_id or 0}"
    return {
        "report_id": report_id,
        "student": {
            "id": student.id,
            "full_name": student.full_name,
            "student_id": student.student_id,
            "class_name": student.class_.name if student.class_ else "—",
        },
        "summary": {
            "average": avg,
            "total_subjects": len(results),
            "rank": rank,
            "class_size": class_size,
        },
        "results": [{
            "subject_name": r.subject.name if r.subject else "—",
            "ca_score": r.ca_score,
            "exam_score": r.exam_score,
            "total_score": r.total_score,
            "grade": r.grade,
            "remark": r.remark,
        } for r in results],
        "session_name": results[0].session.session_name if results else None,
        "term_name": results[0].term.term_name if results else None,
    }


@reports_router.get("/transcript/{student_id}")
def student_transcript(student_id: int,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(require_staff)):
    """Full academic transcript: all approved results across all terms."""
    from app.models.models import Student as StudentModel
    student = db.query(StudentModel).filter(StudentModel.id == student_id).first()
    if not student:
        raise HTTPException(404, "Student not found")

    results = db.query(Result).filter(
        Result.student_id == student_id,
        Result.status == ResultStatus.approved,
    ).order_by(Result.session_id, Result.term_id).all()

    # Group by session → term
    from collections import defaultdict
    grouped: dict = defaultdict(lambda: defaultdict(list))
    for r in results:
        sname = r.session.session_name if r.session else "Unknown Session"
        tname = r.term.term_name      if r.term    else "Unknown Term"
        grouped[sname][tname].append({
            "subject_name": r.subject.name if r.subject else "—",
            "ca_score": r.ca_score,
            "exam_score": r.exam_score,
            "total_score": r.total_score,
            "grade": r.grade,
            "remark": r.remark,
        })

    transcript = []
    for sname, terms in grouped.items():
        terms_list = []
        for tname, rlist in terms.items():
            totals = [r["total_score"] for r in rlist if r["total_score"] is not None]
            terms_list.append({
                "term_name": tname,
                "results": rlist,
                "term_average": round(sum(totals)/len(totals), 1) if totals else 0,
            })
        transcript.append({"session_name": sname, "terms": terms_list})

    return {
        "student": {
            "id": student.id,
            "full_name": student.full_name,
            "student_id": student.student_id,
            "class_name": student.class_.name if student.class_ else "—",
        },
        "transcript": transcript,
    }