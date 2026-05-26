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
