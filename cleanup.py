"""
JAASIEL RMS — Cleanup Script
Removes demo/seed students, all seeded subjects, and their
class-subject links from the database.

Usage: python cleanup.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.db.base import SessionLocal
from app.models.models import Student, Subject, ClassSubject

# ── Usernames of the demo students created by seed.py ────────
DEMO_USERNAMES = [
    "eghosavictoraisosa12",
    "funkeadebayojohnson11",
    "chidiokonkwonze12",
    "amakachukwuobi11",
    "segunadewalelawal12",
    "ngozichinweeze10",
    "tundebiodunbalogun09",
    "yetundeoluwaseunadesanya10",
    "iyereifeanyinwachukwu13",
    "blessinguchennaokoro13",
]

# ── Names of the subjects created by seed.py ─────────────────
SEED_SUBJECTS = [
    "Mathematics", "English Language", "Basic Science", "Social Studies",
    "Yoruba Language", "French", "Physical Education", "Fine Arts",
    "Computer Science", "Agricultural Science", "Chemistry", "Biology",
    "Physics", "Further Mathematics", "Economics", "Government",
    "Christian Religious Studies", "Islamic Religious Studies",
    "Civic Education", "Technical Drawing", "Food and Nutrition",
    "Accounting", "Commerce", "Literature in English",
]


def cleanup():
    db = SessionLocal()
    try:
        print("🧹 Running Jaasiel RMS cleanup...")

        # ── Remove demo students ──────────────────────────────────
        removed_students = 0
        for username in DEMO_USERNAMES:
            student = db.query(Student).filter(Student.username == username).first()
            if student:
                db.delete(student)
                removed_students += 1
        print(f"  ✅ {removed_students} demo student(s) removed")

        # ── Remove class-subject links for seed subjects ──────────
        removed_links = 0
        for name in SEED_SUBJECTS:
            subject = db.query(Subject).filter(Subject.name == name).first()
            if subject:
                links = db.query(ClassSubject).filter(
                    ClassSubject.subject_id == subject.id
                ).all()
                for link in links:
                    db.delete(link)
                    removed_links += 1
        print(f"  ✅ {removed_links} class-subject link(s) removed")

        # ── Remove seed subjects ──────────────────────────────────
        removed_subjects = 0
        for name in SEED_SUBJECTS:
            subject = db.query(Subject).filter(Subject.name == name).first()
            if subject:
                db.delete(subject)
                removed_subjects += 1
        print(f"  ✅ {removed_subjects} seed subject(s) removed")

        db.commit()
        print()
        print("━" * 55)
        print("🎉  CLEANUP COMPLETE!")
        print("━" * 55)
        print()
        print("  Demo students, seed subjects and their class links")
        print("  have been removed. Your real data is untouched.")
        print()
        print("  You can now add your own subjects and students via")
        print("  the admin UI and they will save normally.")
        print()

    except Exception as e:
        db.rollback()
        print(f"\n❌ Error during cleanup: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    cleanup()