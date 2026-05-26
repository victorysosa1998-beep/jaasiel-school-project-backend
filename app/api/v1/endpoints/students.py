"""Students endpoint — CRUD, bulk upload, credential generation, student self-service."""
import os, uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from app.db.base import get_db
from app.models.models import Student, Class, Result, ResultStatus, AuditLog, User, UserRole
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token, decode_token
from app.api.v1.deps import get_current_user, require_staff, require_admin, get_client_ip
from app.utils.grading import generate_student_id, generate_username, generate_default_password
from app.core.config import settings

router = APIRouter()
bearer = HTTPBearer(auto_error=False)

# ── helpers ──────────────────────────────────────────────────

def _get_student_user(credentials, db):
    """Get current student from JWT. Returns Student or raises."""
    if not credentials:
        raise HTTPException(401, "Not authenticated")
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(401, "Invalid token")
    sub = str(payload.get("sub", ""))
    if not sub.startswith("s:"):
        raise HTTPException(403, "Student access only")
    s = db.query(Student).filter(Student.id == int(sub[2:]), Student.is_active == True).first()
    if not s:
        raise HTTPException(401, "Student not found")
    return s

def _student_out(s: Student, include_password: bool = False) -> dict:
    out = {
        "id": s.id, "full_name": s.full_name, "student_id": s.student_id,
        "username": s.username, "first_name": s.first_name, "middle_name": s.middle_name,
        "last_name": s.last_name,
        "date_of_birth": s.date_of_birth.isoformat() if s.date_of_birth else None,
        "gender": s.gender.value if s.gender else None,
        "class_name": s.class_.name if s.class_ else None, "class_id": s.class_id,
        "parent_phone": s.parent_phone, "parent_email": s.parent_email,
        "address": s.address, "photo_url": s.photo_url,
        "is_active": s.is_active, "must_change_pwd": s.must_change_pwd,
        "enrolled_at": s.enrolled_at.isoformat() if s.enrolled_at else None,
    }
    if include_password:
        out["default_password"] = generate_default_password(s.date_of_birth)
    return out

def _next_student_seq(db: Session) -> int:
    return (db.query(Student).count() or 0) + 1

# ── CRUD ─────────────────────────────────────────────────────

@router.get("")
def list_students(
    page: int = Query(1, ge=1), per_page: int = Query(20, le=100),
    search: Optional[str] = None, class_name: Optional[str] = None,
    gender: Optional[str] = None,
    db: Session = Depends(get_db), current_user: User = Depends(require_staff),
):
    q = db.query(Student).filter(Student.is_active == True)
    if search:
        like = f"%{search}%"
        q = q.filter(Student.full_name.ilike(like) | Student.student_id.ilike(like) | Student.username.ilike(like))
    if class_name:
        cls = db.query(Class).filter(Class.name == class_name).first()
        if cls:
            q = q.filter(Student.class_id == cls.id)
    if gender:
        q = q.filter(Student.gender == gender)

    total = q.count()
    male  = db.query(Student).filter(Student.is_active==True, Student.gender=="male").count()
    female= db.query(Student).filter(Student.is_active==True, Student.gender=="female").count()
    classes = db.query(Student.class_id).distinct().count()
    items = q.order_by(Student.full_name).offset((page - 1) * per_page).limit(per_page).all()
    return {"items": [_student_out(s) for s in items], "total": total,
            "page": page, "per_page": per_page, "male_count": male,
            "female_count": female, "class_count": classes}

@router.get("/me")
def student_me(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
               db: Session = Depends(get_db)):
    s = _get_student_user(credentials, db)
    return _student_out(s)

@router.get("/me/results")
def student_my_results(
    session_id: Optional[int] = None, term_id: Optional[int] = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
):
    s = _get_student_user(credentials, db)
    q = db.query(Result).filter(
        Result.student_id == s.id,
        Result.status.in_([ResultStatus.approved, ResultStatus.published, ResultStatus.locked])
    )
    if session_id: q = q.filter(Result.session_id == session_id)
    if term_id:    q = q.filter(Result.term_id == term_id)
    results = q.all()

    # calculate position using Jaasiel scoring formula
    # Average = Total Score ÷ Number of Scored Subjects (exclude blanks)
    from app.utils.grading import compute_subject_total
    if results:
        scored_totals = [
            compute_subject_total(r.first_test, r.second_test, r.ca_score, r.exam_score, r.total_score)
            for r in results
        ]
        scored_totals = [t for t in scored_totals if t is not None]
        overall_total = sum(scored_totals)
        scored_count  = len(scored_totals)
        avg = overall_total / scored_count if scored_count else 0
        term_ids = list({r.term_id for r in results})
        if term_ids:
            all_results = db.query(Result).filter(
                Result.class_id == results[0].class_id,
                Result.term_id.in_(term_ids),
                Result.status.in_([ResultStatus.approved, ResultStatus.published, ResultStatus.locked])
            ).all()
            from collections import defaultdict
            # Group by student, compute average using Jaasiel formula per student
            student_result_map = defaultdict(list)
            for r in all_results:
                student_result_map[r.student_id].append(r)
            student_avgs = {}
            for sid, sresults in student_result_map.items():
                st_totals = [
                    compute_subject_total(r.first_test, r.second_test, r.ca_score, r.exam_score, r.total_score)
                    for r in sresults
                ]
                st_totals = [t for t in st_totals if t is not None]
                if st_totals:
                    student_avgs[sid] = sum(st_totals) / len(st_totals)
            ranked = sorted(student_avgs.keys(), key=lambda sid: student_avgs[sid], reverse=True)
            position = (ranked.index(s.id) + 1) if s.id in ranked else None
            total_students = len(ranked)
        else:
            position = None; total_students = None
    else:
        avg = 0; scored_count = 0; overall_total = 0; position = None; total_students = None

    # Get term settings (resumption date, next term fee)
    term_obj = None
    if term_id:
        from app.models.models import Term as TermModel
        term_obj = db.query(TermModel).filter(TermModel.id == term_id).first()
    elif results:
        from app.models.models import Term as TermModel
        term_obj = db.query(TermModel).filter(TermModel.id == results[0].term_id).first()

    # Get age from date of birth
    age = None
    if s.date_of_birth:
        from datetime import date
        today = date.today()
        dob = s.date_of_birth.date() if hasattr(s.date_of_birth, 'date') else s.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    # Determine next term fee for this student's class
    next_fee = None
    if term_obj and term_obj.next_term_fee and s.class_:
        fees = term_obj.next_term_fee
        next_fee = fees.get(s.class_.name) or fees.get("all") or None

    # Build photo URL
    photo_url = s.photo_url
    if photo_url and not photo_url.startswith(('http', '/')):
        photo_url = f"/uploads/{photo_url}"

    return {
        "student_id": s.id,
        "full_name": s.full_name,
        "class_name": s.class_.name if s.class_ else None,
        "class_id": s.class_id,
        "date_of_birth": s.date_of_birth.isoformat() if s.date_of_birth else None,
        "age": age,
        "gender": s.gender.value if s.gender else None,
        "photo_url": photo_url,
        "session_name": results[0].session.session_name if results else None,
        "term_name": results[0].term.term_name if results else None,
        "session_id": results[0].session_id if results else None,
        "term_id": results[0].term_id if results else None,
        "position": position, "total_students": total_students,
        # Jaasiel scoring formula outputs
        "overall_total":  round(overall_total if results else 0, 1),
        "scored_subjects": scored_count if results else 0,
        "average_percent": round(avg, 2),
        "resumption_date": term_obj.resumption_date.isoformat() if term_obj and term_obj.resumption_date else None,
        "next_term_fee": next_fee,
        "results": [{
            "subject_name": r.subject.name if r.subject else "—",
            "first_test":   r.first_test,
            "second_test":  r.second_test,
            "ca_score":     r.ca_score,
            "exam_score":   r.exam_score,
            "total_score":  r.total_score,
            "grade":        r.grade,
            "remark":       r.remark,
            "teacher_comment": r.teacher_comment,
            "admin_comment":   r.admin_comment,
            "attendance":      r.attendance,
            "term_name":    r.term.term_name if r.term else None,
            "session_name": r.session.session_name if r.session else None,
            "term_id":      r.term_id,
            "session_id":   r.session_id,
        } for r in results]
    }

@router.get("/me/sessions")
def student_my_sessions(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
):
    """Return all sessions/terms that have published results for this student."""
    s = _get_student_user(credentials, db)
    from app.models.models import Session as AcSession, Term as TermModel
    results = db.query(Result).filter(
        Result.student_id == s.id,
        Result.status.in_([ResultStatus.approved, ResultStatus.published, ResultStatus.locked])
    ).all()
    seen = {}
    for r in results:
        if r.session_id not in seen:
            sess = db.query(AcSession).filter(AcSession.id == r.session_id).first()
            if sess:
                seen[r.session_id] = {
                    "id": sess.id, "session_name": sess.session_name, "is_current": sess.is_current,
                    "terms": []
                }
        if r.session_id in seen:
            term = db.query(TermModel).filter(TermModel.id == r.term_id).first()
            if term and not any(t["id"] == term.id for t in seen[r.session_id]["terms"]):
                seen[r.session_id]["terms"].append({
                    "id": term.id, "term_name": term.term_name, "is_current": term.is_current
                })
    return {"items": sorted(seen.values(), key=lambda x: x["id"], reverse=True)}


@router.post("/me/change-password")
def student_change_password(
    body: dict,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
):
    s = _get_student_user(credentials, db)
    current = body.get("current_password", "")
    new_pwd = body.get("new_password", "")
    if not verify_password(current, s.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if len(new_pwd) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    s.password_hash = hash_password(new_pwd)
    s.must_change_pwd = False
    db.commit()
    return {"message": "Password updated"}

@router.get("/{student_id}")
def get_student(student_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(require_staff)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s: raise HTTPException(404, "Student not found")
    return _student_out(s, include_password=True)

@router.post("")
def create_student(body: dict, db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):
    fn  = (body.get("first_name") or "").strip()
    mn  = (body.get("middle_name") or "").strip()
    ln  = (body.get("last_name") or "").strip()
    full= body.get("full_name") or f"{fn} {mn} {ln}".strip()
    dob_str = body.get("date_of_birth")
    dob = None
    if dob_str:
        try:
            from dateutil import parser as dateparser
            dob = dateparser.parse(dob_str)
        except Exception: pass

    username = body.get("username") or generate_username(fn, mn, ln, dob)
    # ensure unique
    base, n = username, 1
    while db.query(Student).filter(Student.username == username).first():
        username = f"{base}{n}"; n += 1

    default_pwd = body.get("default_password") or generate_default_password(dob)
    pwd_hash = hash_password(default_pwd)

    # resolve class
    class_id = None
    cls_name = body.get("class_name")
    if cls_name:
        cls = db.query(Class).filter(Class.name == cls_name).first()
        if not cls:
            cls = Class(name=cls_name); db.add(cls); db.flush()
        class_id = cls.id

    year = dob.year if dob else datetime.now().year
    seq = _next_student_seq(db)
    stu_id = generate_student_id(year, cls_name or "GEN", seq)

    gender_val = body.get("gender")
    from app.models.models import Gender
    gender = Gender(gender_val) if gender_val and gender_val in [g.value for g in Gender] else None

    student = Student(
        full_name=full, first_name=fn, middle_name=mn, last_name=ln,
        student_id=stu_id, username=username, password_hash=pwd_hash,
        date_of_birth=dob, gender=gender, class_id=class_id,
        parent_phone=body.get("parent_phone"), parent_email=body.get("parent_email"),
        address=body.get("address"), photo_url=body.get("photo_url"),
    )
    db.add(student)
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="create",
                    entity_type="student", description=f"Registered student {full}"))
    db.commit()
    db.refresh(student)
    return _student_out(student, include_password=True)

@router.put("/{student_id}")
def update_student(student_id: int, body: dict, db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s: raise HTTPException(404, "Student not found")
    for field in ["full_name","first_name","middle_name","last_name","parent_phone","parent_email","address","photo_url"]:
        if field in body: setattr(s, field, body[field])
    if "class_name" in body:
        cls = db.query(Class).filter(Class.name == body["class_name"]).first()
        if cls: s.class_id = cls.id
    if "gender" in body:
        from app.models.models import Gender
        try: s.gender = Gender(body["gender"])
        except ValueError: pass
    if "new_password" in body and body["new_password"]:
        from passlib.context import CryptContext
        pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        s.hashed_password = pwd_ctx.hash(body["new_password"])
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="update",
                    entity_type="student", entity_id=student_id,
                    description=f"Updated student {s.full_name}"))
    db.commit(); db.refresh(s)
    return _student_out(s)


@router.post("/{student_id}/photo")
async def upload_student_photo(
    student_id: int,
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Upload or replace a student's profile photo."""
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s: raise HTTPException(404, "Student not found")

    # Validate file type
    allowed = {"image/jpeg", "image/png", "image/webp"}
    ct = photo.content_type or ""
    ext = photo.filename.rsplit(".", 1)[-1].lower() if "." in (photo.filename or "") else "jpg"
    if ct not in allowed and ext not in ("jpg", "jpeg", "png", "webp"):
        raise HTTPException(400, "Only JPG, PNG or WebP images are allowed")

    content = await photo.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "Photo must be under 5MB")

    # Resize to max 400x400 using Pillow to save storage
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(content))
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P": img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA","LA") else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((400, 400), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        content = buf.getvalue()
        ext = "jpg"
    except Exception as e:
        print(f"Photo resize failed: {e} — saving original")

    # Save to uploads dir
    import os, uuid
    from app.core.config import settings
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    filename = f"student_{student_id}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(settings.UPLOAD_DIR, filename)

    # Delete old photo file if it exists
    if s.photo_url:
        old_path = s.photo_url.lstrip("/")
        if os.path.exists(old_path):
            try: os.remove(old_path)
            except: pass

    with open(filepath, "wb") as f:
        f.write(content)

    photo_url = f"/uploads/{filename}"
    s.photo_url = photo_url
    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name,
        user_role=current_user.role.value, action="update",
        entity_type="student", entity_id=student_id,
        description=f"Uploaded photo for student {s.full_name}",
    ))
    db.commit(); db.refresh(s)
    return {"photo_url": photo_url, "message": "Photo uploaded successfully"}

@router.delete("/{student_id}")
def delete_student(student_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_admin)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s: raise HTTPException(404, "Student not found")
    s.is_active = False
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="delete",
                    entity_type="student", entity_id=student_id,
                    description=f"Deactivated student {s.full_name}"))
    db.commit()
    return {"message": "Student deactivated"}

@router.get("/{student_id}/results")
def student_results(student_id: int, session_id: Optional[int] = None,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_staff)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s: raise HTTPException(404, "Student not found")
    q = db.query(Result).filter(Result.student_id == student_id)
    if session_id: q = q.filter(Result.session_id == session_id)
    results = q.all()
    return {"student": _student_out(s), "results": [
        {"subject_name": r.subject.name if r.subject else "—",
         "ca_score": r.ca_score, "exam_score": r.exam_score,
         "total_score": r.total_score, "grade": r.grade, "remark": r.remark,
         "status": r.status.value,
         "session_name": r.session.session_name if r.session else None,
         "term_name": r.term.term_name if r.term else None} for r in results
    ]}

@router.post("/bulk-upload")
async def bulk_upload(
    file: UploadFile = File(...),
    class_name: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """
    Accept Excel/CSV/image/PDF of a student list.
    - Excel/CSV: parsed directly with smart column detection
    - Image/PDF:  sent to Claude Vision API for extraction
    Returns a preview list for the frontend to review before saving.
    """
    content = await file.read()
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""

    students_raw = []
    try:
        if ext in ("xlsx", "xls"):
            students_raw = _parse_spreadsheet_excel(content)
        elif ext == "csv":
            students_raw = _parse_spreadsheet_csv(content)
        elif ext in ("jpg", "jpeg", "png", "webp", "pdf"):
            students_raw = await _extract_students_with_claude(content, file.content_type or "image/jpeg")
        else:
            raise HTTPException(400, f"Unsupported file type '.{ext}'. Use Excel, CSV, JPG, PNG or PDF.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Failed to parse file: {str(e)}")

    if not students_raw:
        raise HTTPException(422,
            "No students could be extracted from this file. "
            "For Excel/CSV: check the file has data rows (not just a header). "
            "For images: ensure the photo is clear and well-lit."
        )

    # Build output with generated credentials
    students_out = []
    for s in students_raw:
        fn   = (s.get("first_name")  or "").strip()
        mn   = (s.get("middle_name") or "").strip()
        ln   = (s.get("last_name")   or "").strip()
        full = (s.get("full_name")   or "").strip() or f"{fn} {mn} {ln}".strip()

        # If Claude only returned full_name, split it intelligently
        if full and not fn:
            fn, mn, ln = _split_name(full)
        elif fn and not full:
            full = f"{fn} {mn} {ln}".strip()

        dob_str = str(s.get("date_of_birth") or s.get("dob") or "").strip()
        gender  = str(s.get("gender") or "").strip()

        dob = None
        if dob_str:
            try:
                from dateutil import parser as dp
                dob = dp.parse(str(dob_str))
            except Exception:
                pass

        username = generate_username(fn, mn, ln, dob)
        # Ensure username unique
        base, n = username, 1
        existing_usernames = {u for u, in db.query(Student.username).all()}
        while username in existing_usernames:
            username = f"{base}{n}"; n += 1

        password = generate_default_password(dob)

        students_out.append({
            "full_name":   full,
            "first_name":  fn,
            "middle_name": mn,
            "last_name":   ln,
            "date_of_birth": dob.date().isoformat() if dob else (dob_str or None),
            "dob":         dob.date().isoformat() if dob else (dob_str or None),
            "gender":      gender,
            "parent_phone": str(s.get("parent_phone") or s.get("phone") or "").strip() or None,
            "parent_email": str(s.get("parent_email") or s.get("email") or "").strip() or None,
            "username":    username,
            "password":    password,
            "class_name":  class_name,
        })

    return {"students": students_out, "total": len(students_out)}


# ── Spreadsheet parsers ──────────────────────────────────────────────────────

def _detect_col(headers: list[str], keywords: list[str]) -> int | None:
    """Return column index whose header contains any keyword (case-insensitive)."""
    for kw in keywords:
        for i, h in enumerate(headers):
            if kw in h.lower():
                return i
    return None

def _parse_spreadsheet_excel(content: bytes) -> list[dict]:
    import openpyxl, io
    wb  = openpyxl.load_workbook(io.BytesIO(content))
    ws  = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(c).strip().lower() if c else "" for c in rows[0]]
    return _rows_to_students(headers, rows[1:])

def _parse_spreadsheet_csv(content: bytes) -> list[dict]:
    import csv, io
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.reader(io.StringIO(text))
    rows   = list(reader)
    if not rows:
        return []
    headers = [h.strip().lower() for h in rows[0]]
    return _rows_to_students(headers, rows[1:])

def _split_name(full: str) -> tuple[str, str, str]:
    """
    Split a full name string into (first, middle, last).
    Rule: first word = first name, last word = last name, everything else = middle.
    Works for any number of words, hyphenated names, O'surnames, etc.
    """
    parts = full.strip().split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    # 3+ words: first, middle(s), last
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def _rows_to_students(headers: list[str], data_rows) -> list[dict]:
    """
    Smart column detection — works regardless of column order or presence.
    Handles: full name only, first+last only, first+middle+last, numbered lists, etc.
    """
    col_fn     = _detect_col(headers, ["first name", "firstname", "first"])
    col_mn     = _detect_col(headers, ["middle name", "middlename", "middle", "other name"])
    col_ln     = _detect_col(headers, ["last name", "lastname", "surname", "family name"])
    col_full   = _detect_col(headers, ["full name", "fullname", "student name", "name", "pupil"])
    col_dob    = _detect_col(headers, ["date of birth", "dob", "birth date", "birthday", "birth"])
    col_gender = _detect_col(headers, ["gender", "sex"])
    col_phone  = _detect_col(headers, ["phone", "mobile", "parent phone", "contact"])
    col_email  = _detect_col(headers, ["email", "parent email", "mail"])

    def _cell(row, idx):
        if idx is None: return ""
        try:
            v = row[idx]
            return str(v).strip() if v is not None else ""
        except: return ""

    # If there are no recognisable headers at all (e.g. plain numbered list),
    # treat the first non-numeric, non-empty cell in each row as the full name
    no_headers = all(h == "" for h in headers)

    students = []
    for row in data_rows:
        cells = [str(c).strip() if c is not None else "" for c in row]
        if not any(cells):
            continue  # fully blank row

        if no_headers:
            # Plain list: find first cell that looks like a name (not a number)
            name_cell = next((c for c in cells if c and not c.replace(".","").isdigit()), "")
            if not name_cell:
                continue
            fn, mn, ln = _split_name(name_cell)
            students.append({
                "full_name":     name_cell,
                "first_name":    fn,
                "middle_name":   mn,
                "last_name":     ln,
                "date_of_birth": cells[1] if len(cells) > 1 else None,
                "gender":        cells[2] if len(cells) > 2 else None,
                "parent_phone":  None,
                "parent_email":  None,
            })
            continue

        fn   = _cell(row, col_fn)
        mn   = _cell(row, col_mn)
        ln   = _cell(row, col_ln)
        full = _cell(row, col_full)

        # Build full name from parts if we have them
        if fn or ln:
            full = f"{fn} {mn} {ln}".strip()
        elif full:
            # Only full name column — split it smartly
            fn, mn, ln = _split_name(full)

        if not full:
            continue  # nothing useful

        students.append({
            "full_name":     full,
            "first_name":    fn,
            "middle_name":   mn,
            "last_name":     ln,
            "date_of_birth": _cell(row, col_dob) or None,
            "gender":        _cell(row, col_gender) or None,
            "parent_phone":  _cell(row, col_phone) or None,
            "parent_email":  _cell(row, col_email) or None,
        })
    return students


# ── Claude Vision extraction for images/PDFs ─────────────────────────────────

STUDENT_LIST_PROMPT = """You are an AI assistant that extracts student names from any kind of document — handwritten class register, typed list, scanned sheet, numbered list, table with headers, or plain text.

Your job: find every student name in the image and return it with the name correctly split into parts.

NAME SPLITTING RULES (apply intelligently regardless of format):
- The FIRST word is always the first name
- The LAST word is always the last name / surname
- Any word(s) in between are middle name(s)
- Examples:
    "Emily Davies"         → first: Emily,    middle: null,        last: Davies
    "Aisha Mohammed"       → first: Aisha,    middle: null,        last: Mohammed
    "Samuel Johnson"       → first: Samuel,   middle: null,        last: Johnson
    "Mohammed Al-Fayed"    → first: Mohammed, middle: null,        last: Al-Fayed
    "Eghosa Victor Aisosa" → first: Eghosa,   middle: Victor,      last: Aisosa
    "Mary Ann Jane Smith"  → first: Mary,     middle: Ann Jane,    last: Smith

DOCUMENT FORMAT HANDLING — be smart about all of these:
- Numbered list (1. Emily Davies, 2. Liam O'Connell) → strip the number, extract the name
- Table with columns (Name | DOB | Gender | Phone) → extract from each column
- Plain list with no numbers → each line is one student
- Handwritten register → read carefully, include all visible names
- Mixed case or ALL CAPS → preserve as written
- Hyphenated names (Al-Fayed, O'Connell) → keep together as one name part

IF the document has extra columns (DOB, gender, phone, etc.) → extract those too.
IF there are no extra columns → set those fields to null. Never invent data.

SKIP: title rows, column headers, page numbers, totals, blank rows.

Return ONLY a raw JSON array. No markdown, no explanation, no code fences.
Each object must have exactly these fields:
{
  "full_name":     "complete name exactly as written on document",
  "first_name":    "first word of name",
  "middle_name":   "middle word(s) or null if only 2 words",
  "last_name":     "last word of name",
  "date_of_birth": "as written or null",
  "gender":        "Male or Female or null",
  "parent_phone":  "phone number or null",
  "parent_email":  "email or null"
}
"""

async def _extract_students_with_claude(image_bytes: bytes, content_type: str) -> list[dict]:
    """Use Claude claude-sonnet-4-5 Vision to extract student list from image."""
    import os, base64, json, httpx
    from app.core.config import settings

    api_key = getattr(settings, "ANTHROPIC_API_KEY", None) or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-ant-your"):
        raise HTTPException(503,
            "ANTHROPIC_API_KEY is not configured. "
            "Add it to your .env file to enable image/PDF extraction. "
            "For now please use Excel or CSV format."
        )

    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 4096,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": content_type, "data": b64},
                },
                {"type": "text", "text": STUDENT_LIST_PROMPT},
            ],
        }],
    }

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type":      "application/json",
            },
            json=payload,
        )

    resp.raise_for_status()
    raw = ""
    for block in resp.json().get("content", []):
        if block.get("type") == "text":
            raw += block["text"]

    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    result = json.loads(raw)
    return result if isinstance(result, list) else []