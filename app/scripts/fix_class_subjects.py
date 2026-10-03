"""
app/scripts/fix_class_subjects.py

One-off data fix for Jaasiel RMS, meant to run automatically on
app startup after you push to GitHub and Railway redeploys.

Fixes (still active):
  1. Adds "C.R.S" to KG 1 and KG 2
  2. Creates "Business Studies" subject if it doesn't exist yet

REMOVED on 2026-10 (they would undo the new subject lists on every deploy —
see app/scripts/update_class_subjects.py):
  - Adding "C.R.S" to KG 3
  - Giving KG 3 every subject that "Basic 1" has
  - Adding "Basic Science" / "Basic Technology" / "Business Studies" to
    "Jss 1" and "Jss 2"
Their one-time effects were already applied to the database; removing the
code does not delete anything from the database.

Safe to leave in permanently — every statement uses
ON CONFLICT DO NOTHING (or an equivalent NOT EXISTS check), so after
the first successful run it just does nothing on every future deploy
(no duplicates, nothing deleted).
"""

from sqlalchemy import text
from app.db.base import SessionLocal


FIXES = [
    (
        "Add C.R.S to KG 1, KG 2",
        """
        INSERT INTO class_subjects (class_id, subject_id)
        SELECT c.id, s.id
        FROM classes c, subjects s
        WHERE c.name IN ('KG 1', 'KG 2')
          AND s.name = 'C.R.S'
        ON CONFLICT (class_id, subject_id) DO NOTHING
        """,
    ),
    (
        "Create 'Business Studies' subject if it doesn't exist",
        """
        INSERT INTO subjects (name, is_active, created_at)
        SELECT 'Business Studies', TRUE, NOW()
        WHERE NOT EXISTS (SELECT 1 FROM subjects WHERE name = 'Business Studies')
        """,
    ),
]


def run_class_subject_fixes():
    """Runs each fix in its own try block so one bad match can't
    block the others or crash app startup."""
    db = SessionLocal()
    try:
        print("[fix_class_subjects] Starting class-subject data fix...")
        for description, sql in FIXES:
            try:
                result = db.execute(text(sql))
                db.commit()
                print(f"[fix_class_subjects] {description}: {result.rowcount} row(s) inserted")
            except Exception as e:
                db.rollback()
                print(f"[fix_class_subjects] FAILED — {description}: {e}")
        print("[fix_class_subjects] Done.")
    finally:
        db.close()


if __name__ == "__main__":
    # Lets you also run it manually with: python -m app.scripts.fix_class_subjects
    run_class_subject_fixes()