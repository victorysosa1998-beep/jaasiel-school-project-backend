"""
app/scripts/fix_class_subjects.py

One-off data fix for Jaasiel RMS, meant to run automatically on
app startup after you push to GitHub and Railway redeploys.

Fixes:
  1. Adds "C.R.S" to KG 1, KG 2, KG 3
  2. Gives KG 3 every subject that "Basic 1" has
  3. Adds "Basic Science" to "Jss 1"

Safe to leave in permanently — every statement uses
ON CONFLICT DO NOTHING, so after the first successful run it just
does nothing on every future deploy (no duplicates, nothing deleted).
"""

from sqlalchemy import text
from app.db.base import SessionLocal


FIXES = [
    (
        "Add C.R.S to KG 1, KG 2, KG 3",
        """
        INSERT INTO class_subjects (class_id, subject_id)
        SELECT c.id, s.id
        FROM classes c, subjects s
        WHERE c.name IN ('KG 1', 'KG 2', 'KG 3')
          AND s.name = 'C.R.S'
        ON CONFLICT (class_id, subject_id) DO NOTHING
        """,
    ),
    (
        "Give KG 3 every subject that Basic 1 has",
        """
        INSERT INTO class_subjects (class_id, subject_id)
        SELECT (SELECT id FROM classes WHERE name = 'KG 3'), cs.subject_id
        FROM class_subjects cs
        JOIN classes c ON cs.class_id = c.id
        WHERE c.name = 'Basic 1'
        ON CONFLICT (class_id, subject_id) DO NOTHING
        """,
    ),
    (
        "Add Basic Science to Jss 1",
        """
        INSERT INTO class_subjects (class_id, subject_id)
        SELECT c.id, s.id
        FROM classes c, subjects s
        WHERE c.name = 'Jss 1'
          AND s.name = 'Basic Science'
        ON CONFLICT (class_id, subject_id) DO NOTHING
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