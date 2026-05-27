"""Authentication — login for admins, sub-admins, and students."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.db.base import get_db
from app.models.models import User, Student, UserRole, AuditLog, LoginSession
from app.core.security import verify_password, hash_password, create_access_token, create_refresh_token, decode_token
from app.api.v1.deps import get_current_user, get_client_ip

router = APIRouter()

class LoginRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = None

class RefreshRequest(BaseModel):
    refresh_token: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

def _user_dict(u: User) -> dict:
    return {"id": u.id, "full_name": u.full_name, "username": u.username,
            "email": u.email, "role": u.role.value, "phone": u.phone}

def _student_dict(s: Student) -> dict:
    return {"id": s.id, "full_name": s.full_name, "username": s.username,
            "student_id": s.student_id,
            "class_name": s.class_.name if s.class_ else None, "role": "student"}

@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent", "")[:300]
    role = (body.role or "").lower()
    identifier = body.username.strip().lower()

    if role == "student":
        student = (db.query(Student)
                   .filter(Student.username == identifier, Student.is_active == True).first())
        if not student or not verify_password(body.password, student.password_hash):
            raise HTTPException(401, "Invalid student credentials")
        sub = f"s:{student.id}"
        access  = create_access_token({"sub": sub, "role": "student"})
        refresh = create_refresh_token({"sub": sub})
        db.add(LoginSession(student_id=student.id, user_name=student.full_name,
                            role="student", ip_address=ip, device=ua))
        db.commit()
        return {"access_token": access, "refresh_token": refresh,
                "token_type": "bearer", "user": _student_dict(student)}
    else:
        user = (db.query(User).filter(User.username == identifier).first()
                or db.query(User).filter(User.email == identifier).first())
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(401, "Invalid credentials")
        if not user.is_active:
            raise HTTPException(403, "Account is inactive")
        sub = f"u:{user.id}"
        access  = create_access_token({"sub": sub, "role": user.role.value})
        refresh = create_refresh_token({"sub": sub})
        db.add(LoginSession(user_id=user.id, user_name=user.full_name,
                            role=user.role.value, ip_address=ip, device=ua))
        db.add(AuditLog(user_id=user.id, user_name=user.full_name, user_role=user.role.value,
                        action="login", description="User logged in", ip_address=ip))
        db.commit()
        return {"access_token": access, "refresh_token": refresh,
                "token_type": "bearer", "user": _user_dict(user)}

@router.post("/logout")
def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = (db.query(LoginSession)
               .filter(LoginSession.user_id == current_user.id, LoginSession.status == "active")
               .order_by(LoginSession.login_time.desc()).first())
    if session:
        session.status = "ended"
        session.logout_time = datetime.now(timezone.utc)
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="logout", description="User logged out"))
    db.commit()
    return {"message": "Logged out successfully"}

@router.post("/refresh")
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid refresh token")
    sub = str(payload.get("sub", ""))
    if sub.startswith("s:"):
        s = db.query(Student).filter(Student.id == int(sub[2:])).first()
        if not s: raise HTTPException(401, "Student not found")
        access  = create_access_token({"sub": sub, "role": "student"})
        refresh = create_refresh_token({"sub": sub})
    else:
        uid = int(sub.replace("u:", "")) if sub.startswith("u:") else int(sub)
        u = db.query(User).filter(User.id == uid).first()
        if not u: raise HTTPException(401, "User not found")
        access  = create_access_token({"sub": sub, "role": u.role.value})
        refresh = create_refresh_token({"sub": sub})
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}

@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return _user_dict(current_user)

@router.post("/change-password")
def change_password(body: ChangePasswordRequest,
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if len(body.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    current_user.password_hash = hash_password(body.new_password)
    db.add(AuditLog(user_id=current_user.id, user_name=current_user.full_name,
                    user_role=current_user.role.value, action="password_change",
                    description="Password changed"))
    db.commit()
    return {"message": "Password updated successfully"}

@router.post("/student-change-password")
def student_change_password(
    body: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Password change endpoint that works with student JWT tokens."""
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = auth_header[7:]
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(401, "Invalid or expired token")
    sub = str(payload.get("sub", ""))
    if sub.startswith("s:"):
        student = db.query(Student).filter(Student.id == int(sub[2:]), Student.is_active == True).first()
        if not student: raise HTTPException(401, "Student not found")
        if not verify_password(body.current_password, student.password_hash):
            raise HTTPException(400, "Current password is incorrect")
        if len(body.new_password) < 6:
            raise HTTPException(400, "Password must be at least 6 characters")
        student.password_hash = hash_password(body.new_password)
        student.must_change_pwd = False
        db.commit()
        return {"message": "Password updated successfully"}
    else:
        raise HTTPException(403, "Use /auth/change-password for staff accounts")


# ══════════════════════════════════════════════════════════════
# PASSWORD RESET  (public — no token required)
#
# Students:
#   Verify: username + full_name + birth_year + birth_month
#   Reset to: DDMMYY  (same as generate_default_password in grading.py)
#   e.g. born 15 March 2005 → password = "150305"
#
# Staff (admin / sub-admin):
#   No date_of_birth stored → verify full_name + school PIN
#   Reset to: their username
#   School PIN is set via Railway env var SCHOOL_RESET_PIN
#   (default: JaasielRMS if env var not set)
# ══════════════════════════════════════════════════════════════

import os
from app.utils.grading import generate_default_password

class ResetPasswordRequest(BaseModel):
    username:    str
    full_name:   str
    birth_year:  Optional[int] = None   # students only
    birth_month: Optional[int] = None   # students only (1–12)
    school_pin:  Optional[str] = None   # staff only


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Public endpoint — no JWT needed.
    Verifies identity then resets to the original default password.
    """
    username  = body.username.strip().lower()
    full_name = body.full_name.strip()

    # ── Try student first ──────────────────────────────────────
    student = db.query(Student).filter(
        Student.username == username,
        Student.is_active == True
    ).first()

    if student:
        # Verify full name (case-insensitive)
        if student.full_name.strip().lower() != full_name.lower():
            raise HTTPException(400, "Details do not match our records")

        # Birth year + month are required for students
        if body.birth_year is None or body.birth_month is None:
            raise HTTPException(400, "Birth year and month are required for student accounts")

        if student.date_of_birth is None:
            raise HTTPException(400, "Date of birth not recorded for this account. Please contact admin.")

        dob = student.date_of_birth
        if dob.year != body.birth_year or dob.month != body.birth_month:
            raise HTTPException(400, "Details do not match our records")

        # Reset to DDMMYY — same function used at registration (grading.py)
        new_password = generate_default_password(dob)
        student.password_hash  = hash_password(new_password)
        student.must_change_pwd = True   # prompt to change on next login
        db.add(AuditLog(
            action="password_reset",
            entity_type="student",
            entity_id=student.id,
            description=f"Self-service password reset for student {student.username}"
        ))
        db.commit()
        return {
            "message": "Password reset successful. Your new password is your date of birth (DDMMYY).",
            "role": "student",
            "hint": f"{dob.day:02d}{dob.month:02d}{str(dob.year)[-2:]}"   # show them the password
        }

    # ── Try staff (admin / sub-admin) ─────────────────────────
    staff = (
        db.query(User).filter(User.username == username, User.is_active == True).first()
        or db.query(User).filter(User.email == username, User.is_active == True).first()
    )

    if staff:
        if staff.full_name.strip().lower() != full_name.lower():
            raise HTTPException(400, "Details do not match our records")

        school_pin = os.environ.get("SCHOOL_RESET_PIN", "JaasielRMS")
        if not body.school_pin or body.school_pin.strip() != school_pin:
            raise HTTPException(400, "Incorrect school PIN")

        # Staff reset to their username (no DOB available)
        new_password = staff.username
        staff.password_hash = hash_password(new_password)
        db.add(AuditLog(
            user_id=staff.id,
            user_name=staff.full_name,
            user_role=staff.role.value,
            action="password_reset",
            description=f"Self-service password reset for staff {staff.username}"
        ))
        db.commit()
        return {
            "message": "Password reset successful. Your new password is your username.",
            "role": staff.role.value,
            "hint": staff.username
        }

    raise HTTPException(404, "No active account found with that username")