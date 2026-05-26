# """
# JAASIEL RMS — Database Seed Script
# Run this ONCE after setting up the database to create:
#   - Super Admin account
#   - Sub Admin account
#   - Default sessions & terms
#   - Default classes & subjects
#   - Sample students

# Usage: python seed.py
# """
# import sys, os
# sys.path.insert(0, os.path.dirname(__file__))

# # ── IMPORTANT: import models BEFORE create_all so SQLAlchemy
# #    knows about all table definitions ─────────────────────────
# from app.db.base import Base, engine, SessionLocal
# from app.models.models import (          # noqa — must come before create_all
#     User, Student, Class, Subject, ClassSubject,
#     Session as AcSession, Term, SchoolSettings,
#     UserRole, Gender,
# )
# Base.metadata.create_all(bind=engine)   # now all tables are created

# from app.core.security import hash_password
# from app.utils.grading import generate_username, generate_default_password, generate_student_id
# from dateutil import parser as dateparser


# def seed():
#     db = SessionLocal()
#     try:
#         print("🌱 Seeding Jaasiel RMS database...")

#         # ── Super Admin ───────────────────────────────────────────
#         existing_admin = db.query(User).filter(User.username == "superadmin").first()
#         if not existing_admin:
#             db.add(User(
#                 full_name="Super Administrator",
#                 username="superadmin",
#                 email="admin@jaasiel.edu.ng",
#                 phone="+234 703 630 4408",
#                 password_hash=hash_password("Admin@123"),
#                 role=UserRole.super_admin,
#                 is_active=True,
#             ))
#             print("  ✅ Super Admin created — username: superadmin / password: Admin@123")
#         else:
#             print("  ℹ️  Super Admin already exists")

#         # ── Sub Admin ─────────────────────────────────────────────
#         if not db.query(User).filter(User.username == "subadmin").first():
#             db.add(User(
#                 full_name="Iyere A. Love",
#                 username="subadmin",
#                 email="iyere@jaasiel.edu.ng",
#                 phone="+234 801 234 5678",
#                 password_hash=hash_password("SubAdmin@123"),
#                 role=UserRole.sub_admin,
#                 is_active=True,
#             ))
#             print("  ✅ Sub Admin created — username: subadmin / password: SubAdmin@123")
#         else:
#             print("  ℹ️  Sub Admin already exists")

#         db.flush()

#         # ── Classes ───────────────────────────────────────────────
#         CLASS_NAMES = [
#             "Creche", "Daycare", "Pre-Nursery",
#             "KG 1", "KG 2", "KG 3",
#             "Basic 1", "Basic 2", "Basic 3", "Basic 4", "Basic 5",
#             "Basic 7", "Basic 8", "Basic 9",
#             "SS 1", "SS 2", "SS 3",
#         ]
#         class_map = {}
#         for name in CLASS_NAMES:
#             cls = db.query(Class).filter(Class.name == name).first()
#             if not cls:
#                 cls = Class(name=name)
#                 db.add(cls)
#                 db.flush()
#             class_map[name] = cls
#         print(f"  ✅ {len(CLASS_NAMES)} classes ready")

#         # ── Subjects ──────────────────────────────────────────────
#         SUBJECTS = [
#             "Mathematics", "English Language", "Basic Science", "Social Studies",
#             "Yoruba Language", "French", "Physical Education", "Fine Arts",
#             "Computer Science", "Agricultural Science", "Chemistry", "Biology",
#             "Physics", "Further Mathematics", "Economics", "Government",
#             "Christian Religious Studies", "Islamic Religious Studies",
#             "Civic Education", "Technical Drawing", "Food and Nutrition",
#             "Accounting", "Commerce", "Literature in English",
#         ]
#         subject_map = {}
#         for name in SUBJECTS:
#             sub = db.query(Subject).filter(Subject.name == name).first()
#             if not sub:
#                 sub = Subject(name=name)
#                 db.add(sub)
#                 db.flush()
#             subject_map[name] = sub
#         print(f"  ✅ {len(SUBJECTS)} subjects ready")

#         # ── Assign core subjects to all classes ───────────────────
#         CORE = ["Mathematics", "English Language", "Basic Science",
#                 "Social Studies", "Physical Education"]
#         for cls in class_map.values():
#             for sname in CORE:
#                 if sname in subject_map:
#                     exists = db.query(ClassSubject).filter(
#                         ClassSubject.class_id == cls.id,
#                         ClassSubject.subject_id == subject_map[sname].id
#                     ).first()
#                     if not exists:
#                         db.add(ClassSubject(class_id=cls.id,
#                                             subject_id=subject_map[sname].id))
#         print("  ✅ Core subjects assigned to all classes")

#         # ── Session & Terms ───────────────────────────────────────
#         if not db.query(AcSession).filter(AcSession.session_name == "2024/2025").first():
#             session = AcSession(session_name="2024/2025", is_current=True)
#             db.add(session)
#             db.flush()
#             for term_name, is_current in [
#                 ("First Term",  False),
#                 ("Second Term", True),
#                 ("Third Term",  False),
#             ]:
#                 db.add(Term(session_id=session.id,
#                             term_name=term_name,
#                             is_current=is_current))
#             print("  ✅ Session 2024/2025 created (Second Term is current)")
#         else:
#             print("  ℹ️  Session 2024/2025 already exists")

#         # ── Sample Students ───────────────────────────────────────
#         sample_students = [
#             ("Eghosa",   "Victor",    "Aisosa",    "2012-04-20", "male",   "JSS 2"),
#             ("Funke",    "Adebayo",   "Johnson",   "2011-09-15", "female", "JSS 2"),
#             ("Chidi",    "Okonkwo",   "Nze",       "2012-01-08", "male",   "JSS 2"),
#             ("Amaka",    "Chukwu",    "Obi",       "2011-11-22", "female", "JSS 2"),
#             ("Segun",    "Adewale",   "Lawal",     "2012-03-14", "male",   "JSS 3"),
#             ("Ngozi",    "Chinwe",    "Eze",       "2010-07-30", "female", "SS 1"),
#             ("Tunde",    "Biodun",    "Balogun",   "2009-12-05", "male",   "SS 2"),
#             ("Yetunde",  "Oluwaseun", "Adesanya",  "2010-02-18", "female", "SS 2"),
#             ("iyere",    "Ifeanyi",   "Nwachukwu", "2013-06-25", "male",   "Primary 5"),
#             ("Blessing", "Uchenna",   "Okoro",     "2013-08-11", "female", "Primary 5"),
#         ]

#         created_count = 0
#         # Count existing students ONCE before the loop so seq never repeats
#         existing_count = db.query(Student).count()
#         used_ids = set()  # track IDs added in this session (not yet committed)

#         for index, (fn, mn, ln, dob_str, gender, cls_name) in enumerate(sample_students, start=1):
#             dob      = dateparser.parse(dob_str)
#             username = generate_username(fn, mn, ln, dob)
#             if db.query(Student).filter(Student.username == username).first():
#                 continue
#             default_pwd = generate_default_password(dob)
#             cls         = class_map.get(cls_name)

#             # Use existing_count + index so each student gets a unique seq
#             seq    = existing_count + index
#             stu_id = generate_student_id(dob.year, cls_name, seq)

#             # If by chance this ID already exists in DB or this batch, increment
#             while (db.query(Student).filter(Student.student_id == stu_id).first()
#                    or stu_id in used_ids):
#                 seq   += 1
#                 stu_id = generate_student_id(dob.year, cls_name, seq)

#             used_ids.add(stu_id)

#             db.add(Student(
#                 full_name=f"{fn} {mn} {ln}",
#                 first_name=fn, middle_name=mn, last_name=ln,
#                 student_id=stu_id,
#                 username=username,
#                 password_hash=hash_password(default_pwd),
#                 date_of_birth=dob,
#                 gender=Gender(gender),
#                 class_id=cls.id if cls else None,
#                 parent_phone="+234 800 000 0000",
#                 is_active=True,
#                 must_change_pwd=True,
#             ))
#             created_count += 1

#         if created_count:
#             print(f"  ✅ {created_count} sample students created")
#         else:
#             print("  ℹ️  Sample students already exist")

#         # ── School Settings ───────────────────────────────────────
#         defaults = {
#             "school_name":    "Jaasiel Education Centre",
#             "school_address": "Oxygen Street, Benin City, Edo State",
#             "school_phone":   "+234 703 630 4408",
#             "school_email":   "admin@jaasiel.edu.ng",
#             "school_motto":   "Accurate Knowledge is a Virtue",
#             "principal_name": "The Principal",
#         }
#         for key, val in defaults.items():
#             if not db.query(SchoolSettings).filter(SchoolSettings.key == key).first():
#                 db.add(SchoolSettings(key=key, value=val))
#         print("  ✅ School settings saved")

#         db.commit()
#         print()
#         print("━" * 55)
#         print("🎉  SEED COMPLETE! Your Jaasiel RMS is ready.")
#         print("━" * 55)
#         print()
#         print("  LOGIN CREDENTIALS:")
#         print("  Super Admin : superadmin / Admin@123")
#         print("  Sub Admin   : subadmin   / SubAdmin@123")
#         print()
#         print("  Sample student logins:")
#         print("  eghosavictoraisosa12  /  200412")
#         print("  funkeadebayojohnson11 /  150911")
#         print("  chidiokonkwonze12     /  080112")
#         print()
#         print("  Password format: DDMMYY (day+month+last 2 yr of birth)")
#         print()
#         print("  Start server: uvicorn main:app --reload --port 8000")
#         print("  Open browser: http://localhost:8000")
#         print()

#     except Exception as e:
#         db.rollback()
#         print(f"\n❌ Error during seeding: {e}")
#         raise
#     finally:
#         db.close()


# if __name__ == "__main__":
#     seed()











"""
JAASIEL RMS — Database Seed Script
Run this ONCE after setting up the database to create:
  - Super Admin account
  - Sub Admin account
  - Default sessions & terms
  - Default classes

Usage: python seed.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.db.base import Base, engine, SessionLocal
from app.models.models import (
    User, Class,
    Session as AcSession, Term, SchoolSettings,
    UserRole,
)
Base.metadata.create_all(bind=engine)

from app.core.security import hash_password


def seed():
    db = SessionLocal()
    try:
        print("🌱 Seeding Jaasiel RMS database...")

        # ── Super Admin ───────────────────────────────────────────
        if not db.query(User).filter(User.username == "superadmin").first():
            db.add(User(
                full_name="Super Administrator",
                username="superadmin",
                email="admin@jaasiel.edu.ng",
                phone="+234 703 630 4408",
                password_hash=hash_password("Admin@123"),
                role=UserRole.super_admin,
                is_active=True,
            ))
            print("  ✅ Super Admin created — username: superadmin / password: Admin@123")
        else:
            print("  ℹ️  Super Admin already exists")

        # ── Sub Admin ─────────────────────────────────────────────
        if not db.query(User).filter(User.username == "subadmin").first():
            db.add(User(
                full_name="Sub Administrator",
                username="subadmin",
                email="subadmin@jaasiel.edu.ng",
                phone="+234 801 234 5678",
                password_hash=hash_password("SubAdmin@123"),
                role=UserRole.sub_admin,
                is_active=True,
            ))
            print("  ✅ Sub Admin created — username: subadmin / password: SubAdmin@123")
        else:
            print("  ℹ️  Sub Admin already exists")

        db.flush()

        # ── Classes ───────────────────────────────────────────────
        CLASS_NAMES = [
            "Creche", "Daycare", "Pre-Nursery",
            "KG 1", "KG 2", "KG 3",
            "Basic 1", "Basic 2", "Basic 3", "Basic 4", "Basic 5",
            "Basic 7", "Basic 8", "Basic 9",
            "SS 1", "SS 2", "SS 3",
        ]
        created_classes = 0
        for name in CLASS_NAMES:
            if not db.query(Class).filter(Class.name == name).first():
                db.add(Class(name=name))
                created_classes += 1
        db.flush()
        print(f"  ✅ {len(CLASS_NAMES)} classes ready ({created_classes} newly created)")

        # ── Session & Terms ───────────────────────────────────────
        if not db.query(AcSession).filter(AcSession.session_name == "2024/2025").first():
            session = AcSession(session_name="2024/2025", is_current=True)
            db.add(session)
            db.flush()
            for term_name, is_current in [
                ("First Term",  False),
                ("Second Term", False),
                ("Third Term",  True),
            ]:
                db.add(Term(session_id=session.id,
                            term_name=term_name,
                            is_current=is_current))
            print("  ✅ Session 2024/2025 created (Third Term is current)")
        else:
            print("  ℹ️  Session 2024/2025 already exists")

        # ── School Settings ───────────────────────────────────────
        defaults = {
            "school_name":    "Jaasiel Education Centre",
            "school_address": "Oxygen Street, Benin City, Edo State",
            "school_phone":   "+234 703 630 4408",
            "school_email":   "admin@jaasiel.edu.ng",
            "school_motto":   "Accurate Knowledge is a Virtue",
            "director_name": "Jaanwendu Ohaebulam",
        }
        for key, val in defaults.items():
            if not db.query(SchoolSettings).filter(SchoolSettings.key == key).first():
                db.add(SchoolSettings(key=key, value=val))
        print("  ✅ School settings saved")

        db.commit()
        print()
        print("━" * 55)
        print("🎉  SEED COMPLETE! Your Jaasiel RMS is ready.")
        print("━" * 55)
        print()
        print("  LOGIN CREDENTIALS:")
        print("  Super Admin : superadmin / Admin@123")
        print("  Sub Admin   : subadmin   / SubAdmin@123")
        print()
        print("  Next steps:")
        print("  1. Start server : uvicorn main:app --reload --port 8000")
        print("  2. Open browser : http://localhost:8000")
        print("  3. Log in and add your subjects via the UI")
        print("  4. Assign subjects to classes, then start adding students")
        print()

    except Exception as e:
        db.rollback()
        print(f"\n❌ Error during seeding: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()