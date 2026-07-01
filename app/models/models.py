"""
JAASIEL RMS — All SQLAlchemy Models
All 12 tables defined here.
"""
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime,
    ForeignKey, Enum as SAEnum, UniqueConstraint, JSON
)
from sqlalchemy.orm import relationship
from app.db.base import Base


# ══════════════════════════════════════════════════════════════
# ENUMS
# ══════════════════════════════════════════════════════════════

class UserRole(str, enum.Enum):
    super_admin = "super_admin"
    admin       = "admin"
    sub_admin   = "sub_admin"
    student     = "student"

class ResultStatus(str, enum.Enum):
    pending              = "pending"
    submitted            = "submitted"      # sub-admin finished all subjects, pushed to admin
    approved             = "approved"       # admin approved
    rejected             = "rejected"
    correction_requested = "correction_requested"
    locked               = "locked"         # admin locked — no more edits
    published            = "published"

class Gender(str, enum.Enum):
    male   = "male"
    female = "female"
    other  = "other"


# ══════════════════════════════════════════════════════════════
# USERS  (admins, sub-admins — NOT students)
# ══════════════════════════════════════════════════════════════

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    full_name     = Column(String(200), nullable=False)
    username      = Column(String(100), unique=True, index=True, nullable=True)
    email         = Column(String(200), unique=True, index=True, nullable=True)
    phone         = Column(String(30), nullable=True)
    password_hash = Column(String(300), nullable=False)
    role          = Column(SAEnum(UserRole), nullable=False, default=UserRole.sub_admin)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    notifications = relationship("Notification", back_populates="user", foreign_keys="Notification.user_id")


# ══════════════════════════════════════════════════════════════
# CLASSES & SUBJECTS
# ══════════════════════════════════════════════════════════════

class Class(Base):
    __tablename__ = "classes"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), unique=True, nullable=False)
    level       = Column(String(50), nullable=True)   # e.g. "Junior", "Senior", "Primary"
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    students = relationship("Student", back_populates="class_")
    class_subjects = relationship("ClassSubject", back_populates="class_")


class Subject(Base):
    __tablename__ = "subjects"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(200), nullable=False)
    code        = Column(String(20), nullable=True)
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    class_subjects = relationship("ClassSubject", back_populates="subject")


class ClassSubject(Base):
    """Maps which subjects are taught in which classes."""
    __tablename__ = "class_subjects"

    id         = Column(Integer, primary_key=True)
    class_id   = Column(Integer, ForeignKey("classes.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)

    __table_args__ = (UniqueConstraint("class_id", "subject_id"),)

    class_   = relationship("Class",   back_populates="class_subjects")
    subject  = relationship("Subject", back_populates="class_subjects")


# ══════════════════════════════════════════════════════════════
# SESSIONS & TERMS
# ══════════════════════════════════════════════════════════════

class Session(Base):
    __tablename__ = "sessions"

    id           = Column(Integer, primary_key=True, index=True)
    session_name = Column(String(50), unique=True, nullable=False)  # e.g. "2024/2025"
    start_date   = Column(DateTime(timezone=True), nullable=True)
    end_date     = Column(DateTime(timezone=True), nullable=True)
    is_current   = Column(Boolean, default=False)
    created_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    terms    = relationship("Term",    back_populates="session", cascade="all, delete-orphan")
    results  = relationship("Result",  back_populates="session")
    batches  = relationship("ResultBatch", back_populates="session")


class Term(Base):
    __tablename__ = "terms"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    term_name  = Column(String(50), nullable=False)  # "First Term", "Second Term", "Third Term"
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date   = Column(DateTime(timezone=True), nullable=True)
    is_current = Column(Boolean, default=False)
    resumption_date = Column(DateTime(timezone=True), nullable=True)  # next term resumption date
    next_term_fee   = Column(JSON, nullable=True)  # {class_name: fee_amount} dict

    session = relationship("Session", back_populates="terms")
    results = relationship("Result",  back_populates="term")
    batches = relationship("ResultBatch", back_populates="term")


# ══════════════════════════════════════════════════════════════
# STUDENTS
# ══════════════════════════════════════════════════════════════

class Student(Base):
    __tablename__ = "students"

    id               = Column(Integer, primary_key=True, index=True)
    full_name        = Column(String(200), nullable=False)
    first_name       = Column(String(100), nullable=True)
    middle_name      = Column(String(100), nullable=True)
    last_name        = Column(String(100), nullable=True)
    student_id       = Column(String(50), unique=True, index=True, nullable=True)
    username         = Column(String(150), unique=True, index=True, nullable=False)
    password_hash    = Column(String(300), nullable=False)
    date_of_birth    = Column(DateTime(timezone=True), nullable=True)
    gender           = Column(SAEnum(Gender), nullable=True)
    class_id         = Column(Integer, ForeignKey("classes.id"), nullable=True)
    parent_phone     = Column(String(30), nullable=True)
    parent_email     = Column(String(200), nullable=True)
    address          = Column(Text, nullable=True)
    photo_url        = Column(String(500), nullable=True)
    is_active        = Column(Boolean, default=True)
    must_change_pwd  = Column(Boolean, default=True)
    enrolled_at      = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at       = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                              onupdate=lambda: datetime.now(timezone.utc))

    class_   = relationship("Class",   back_populates="students")
    results  = relationship("Result",  back_populates="student")
    login_sessions = relationship("StudentLoginSession", back_populates="student")


class StudentLoginSession(Base):
    __tablename__ = "student_login_sessions"

    id          = Column(Integer, primary_key=True)
    student_id  = Column(Integer, ForeignKey("students.id"))
    login_time  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    logout_time = Column(DateTime(timezone=True), nullable=True)
    ip_address  = Column(String(50), nullable=True)
    status      = Column(String(20), default="active")

    student = relationship("Student", back_populates="login_sessions")


# ══════════════════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════════════════

class ResultBatch(Base):
    """Groups results by sub_admin+class+subject+term for admin approval."""
    __tablename__ = "result_batches"

    id           = Column(Integer, primary_key=True, index=True)
    uploaded_by  = Column(Integer, ForeignKey("users.id"), nullable=False)
    class_id     = Column(Integer, ForeignKey("classes.id"), nullable=False)
    subject_id   = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    session_id   = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    term_id      = Column(Integer, ForeignKey("terms.id"), nullable=False)
    status       = Column(SAEnum(ResultStatus), default=ResultStatus.pending, nullable=False, index=True)
    upload_type  = Column(String(20), default="manual")   # manual | ocr | csv
    admin_note   = Column(Text, nullable=True)
    version      = Column(Integer, default=1)
    has_issues   = Column(Boolean, default=False)
    uploaded_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    approved_at  = Column(DateTime(timezone=True), nullable=True)
    approved_by  = Column(Integer, ForeignKey("users.id"), nullable=True)

    uploader  = relationship("User",    foreign_keys=[uploaded_by])
    approver  = relationship("User",    foreign_keys=[approved_by])
    class_    = relationship("Class",   foreign_keys=[class_id])
    subject   = relationship("Subject", foreign_keys=[subject_id])
    session   = relationship("Session", back_populates="batches", foreign_keys=[session_id])
    term      = relationship("Term",    back_populates="batches", foreign_keys=[term_id])
    results   = relationship("Result",  back_populates="batch")


class Result(Base):
    __tablename__ = "results"

    id          = Column(Integer, primary_key=True, index=True)
    student_id  = Column(Integer, ForeignKey("students.id"), nullable=False)
    class_id    = Column(Integer, ForeignKey("classes.id"),  nullable=False)
    subject_id  = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    session_id  = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    term_id     = Column(Integer, ForeignKey("terms.id"),    nullable=False)
    batch_id    = Column(Integer, ForeignKey("result_batches.id"), nullable=True)

    first_test  = Column(Float, nullable=True)   # out of 20 (optional)
    second_test = Column(Float, nullable=True)   # out of 20 (optional)
    ca_score    = Column(Float, nullable=True)   # combined CA or direct entry
    exam_score  = Column(Float, nullable=True)
    total_score = Column(Float, nullable=True)
    grade       = Column(String(5), nullable=True)
    remark      = Column(String(50), nullable=True)
    position    = Column(Integer, nullable=True)
    teacher_comment = Column(Text, nullable=True)  # class teacher's remark per student
    admin_comment   = Column(Text, nullable=True)  # principal's remark (overrides auto-generated)
    conduct_comment = Column(Text, nullable=True)  # admin's comment on student conduct/behaviour
    attendance      = Column(Integer, nullable=True)  # days school opened (total)
    days_present    = Column(Integer, nullable=True)  # days the student was present
    days_absent     = Column(Integer, nullable=True)  # days the student was absent

    status      = Column(SAEnum(ResultStatus), default=ResultStatus.pending, nullable=False, index=True)
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    admin_note  = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("student_id", "subject_id", "term_id", name="uq_student_subject_term"),
    )

    student  = relationship("Student",     back_populates="results")
    class_   = relationship("Class",       foreign_keys=[class_id])
    subject  = relationship("Subject",     foreign_keys=[subject_id])
    session  = relationship("Session",     back_populates="results", foreign_keys=[session_id])
    term     = relationship("Term",        back_populates="results", foreign_keys=[term_id])
    batch    = relationship("ResultBatch", back_populates="results")
    approver = relationship("User",        foreign_keys=[approved_by])


# ══════════════════════════════════════════════════════════════
# MONTESSORI REPORTS  (Creche / Daycare / Pre-Nursery / KG)
#
# Early-years classes don't use subject scores, averages, grades or
# positions. Instead they're assessed on a set of developmental
# skills/behaviours, each rated 1-3 (see app/utils/montessori_data.py
# for the category list and the meaning of each rating).
# ══════════════════════════════════════════════════════════════

class MontessoriReport(Base):
    __tablename__ = "montessori_reports"

    id          = Column(Integer, primary_key=True, index=True)
    student_id  = Column(Integer, ForeignKey("students.id"), nullable=False)
    class_id    = Column(Integer, ForeignKey("classes.id"),  nullable=False)
    session_id  = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    term_id     = Column(Integer, ForeignKey("terms.id"),    nullable=False)

    # { "Music and Physical Education": {"Shows interest...": 2, ...}, ... }
    ratings     = Column(JSON, nullable=True)

    general_comment      = Column(Text,   nullable=True)  # "HAVE A WONDERFUL HOLIDAY" box
    class_teacher_name   = Column(String(200), nullable=True)
    class_teacher_report = Column(String(300), nullable=True)  # e.g. "Good Result"
    pupils_conduct       = Column(String(300), nullable=True)  # e.g. "Well Behaved"
    proprietors_report   = Column(String(300), nullable=True)  # e.g. "Satisfactory"
    resumption_date      = Column(DateTime(timezone=True), nullable=True)

    status      = Column(SAEnum(ResultStatus), default=ResultStatus.pending, nullable=False, index=True)
    entered_by  = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                         onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("student_id", "term_id", name="uq_montessori_student_term"),
    )

    student = relationship("Student", foreign_keys=[student_id])
    class_  = relationship("Class",   foreign_keys=[class_id])
    session = relationship("Session", foreign_keys=[session_id])
    term    = relationship("Term",    foreign_keys=[term_id])


# ══════════════════════════════════════════════════════════════
# OCR JOBS
# ══════════════════════════════════════════════════════════════

class OcrJob(Base):
    __tablename__ = "ocr_jobs"

    id                = Column(Integer, primary_key=True, index=True)
    uploaded_by       = Column(Integer, ForeignKey("users.id"), nullable=False)
    class_id          = Column(Integer, ForeignKey("classes.id"), nullable=True)
    subject_id        = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    session_id        = Column(Integer, ForeignKey("sessions.id"), nullable=True)
    term_id           = Column(Integer, ForeignKey("terms.id"), nullable=True)
    filename          = Column(String(300), nullable=True)
    filepath          = Column(String(500), nullable=True)
    extraction_status = Column(String(30), default="pending")  # pending|processing|completed|failed
    confidence_score  = Column(Float, nullable=True)
    student_count     = Column(Integer, nullable=True)
    error_message     = Column(Text, nullable=True)
    raw_text          = Column(Text, nullable=True)
    uploaded_at       = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at      = Column(DateTime(timezone=True), nullable=True)

    uploader  = relationship("User",    foreign_keys=[uploaded_by])
    class_    = relationship("Class",   foreign_keys=[class_id])
    subject   = relationship("Subject", foreign_keys=[subject_id])
    rows      = relationship("OcrRow",  back_populates="job", cascade="all, delete-orphan")


class OcrRow(Base):
    __tablename__ = "ocr_rows"

    id                    = Column(Integer, primary_key=True)
    job_id                = Column(Integer, ForeignKey("ocr_jobs.id"), nullable=False)
    extracted_name        = Column(String(300), nullable=True)
    matched_student_id    = Column(Integer, ForeignKey("students.id"), nullable=True)
    match_type            = Column(String(20), default="none")  # full|fuzzy|none
    confidence            = Column(Float, default=0)
    ca_score              = Column(Float, nullable=True)
    exam_score            = Column(Float, nullable=True)
    is_confirmed          = Column(Boolean, default=False)

    job     = relationship("OcrJob",    back_populates="rows")
    student = relationship("Student",   foreign_keys=[matched_student_id])


# ══════════════════════════════════════════════════════════════
# AUDIT LOGS & NOTIFICATIONS
# ══════════════════════════════════════════════════════════════

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=True)
    user_name   = Column(String(200), nullable=True)
    user_role   = Column(String(50), nullable=True)
    action      = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(100), nullable=True)
    entity_id   = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    ip_address  = Column(String(50), nullable=True)
    device      = Column(String(200), nullable=True)
    timestamp   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    user = relationship("User", foreign_keys=[user_id])


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=True)
    student_id  = Column(Integer, ForeignKey("students.id"), nullable=True)
    user_name   = Column(String(200), nullable=True)
    role        = Column(String(50), nullable=True)
    ip_address  = Column(String(50), nullable=True)
    device      = Column(String(300), nullable=True)
    login_time  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    logout_time = Column(DateTime(timezone=True), nullable=True)
    status      = Column(String(20), default="active")

    user    = relationship("User",    foreign_keys=[user_id])
    student = relationship("Student", foreign_keys=[student_id])


class Notification(Base):
    __tablename__ = "notifications"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=True)
    type       = Column(String(50), nullable=True)  # approved|rejected|correction|upload|etc
    title      = Column(String(200), nullable=False)
    message    = Column(Text, nullable=True)
    read       = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user    = relationship("User",    foreign_keys=[user_id], back_populates="notifications")
    student = relationship("Student", foreign_keys=[student_id])


# ══════════════════════════════════════════════════════════════
# SCHOOL SETTINGS
# ══════════════════════════════════════════════════════════════

class SchoolSettings(Base):
    __tablename__ = "school_settings"

    id         = Column(Integer, primary_key=True)
    key        = Column(String(100), unique=True, nullable=False)
    value      = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))