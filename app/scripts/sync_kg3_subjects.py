"""
app/scripts/sync_kg3_subjects.py

Makes KG 3's subject list an EXACT mirror of Basic 1:
  1. Removes any subject currently linked to KG 3 that Basic 1 does NOT have
  2. Adds any subject Basic 1 has that KG 3 is missing

This is different from fix_class_subjects.py, which only ever ADDS
subjects and never removes anything. That earlier script left KG 3's
original "KG courses" in place alongside Basic 1's subjects — this
script actually replaces KG 3's list so it matches Basic 1 exactly.

IMPORTANT — THIS SCRIPT DELETES ROWS:
Every time this runs, KG 3's subject list is forced back to match
Basic 1. If someone manually adds a subject to KG 3 later that Basic 1
doesn't have, this script will remove it again on the next deploy.

If you only want KG 3 set once (and then freely editable afterward),
run this once, confirm it worked, then remove the startup hook for it
(see step 3 below) so it doesn't keep re-syncing on every future
deploy.

WHERE THIS FILE GOES
---------------------
Put it at:  app/scripts/sync_kg3_subjects.py

HOW TO WIRE IT IN (temporary — for one run)
---------------------------------------------
In main.py, add the import:

    from app.scripts.sync_kg3_subjects import sync_kg3_subjects

And call it inside your existing startup event, alongside the other
fix:

    @app.on_event("startup")
    def _startup_data_fixes():
        run_class_subject_fixes()
        sync_kg3_subjects()

Push to GitHub, let Railway redeploy, check the logs for the
"[sync_kg3_subjects]" lines, confirm KG 3 looks right in the app,
then remove the `sync_kg3_subjects()` line (and its import) from
main.py so it doesn't run again on future deploys.
"""

from sqlalchemy import text
from app.db.base import SessionLocal


DELETE_SQL = """
DELETE FROM class_subjects
WHERE class_id = (SELECT id FROM classes WHERE name = 'KG 3')
  AND subject_id NOT IN (
      SELECT cs.subject_id
      FROM class_subjects cs
      JOIN classes c ON cs.class_id = c.id
      WHERE c.name = 'Basic 1'
  )
"""

INSERT_SQL = """
INSERT INTO class_subjects (class_id, subject_id)
SELECT (SELECT id FROM classes WHERE name = 'KG 3'), cs.subject_id
FROM class_subjects cs
JOIN classes c ON cs.class_id = c.id
WHERE c.name = 'Basic 1'
ON CONFLICT (class_id, subject_id) DO NOTHING
"""


def sync_kg3_subjects():
    """Removes KG 3 subjects not in Basic 1, then adds any missing ones,
    so KG 3 ends up an exact match of Basic 1's subject list."""
    db = SessionLocal()
    try:
        print("[sync_kg3_subjects] Starting KG 3 -> Basic 1 sync...")
        try:
            removed = db.execute(text(DELETE_SQL))
            db.commit()
            print(f"[sync_kg3_subjects] Removed {removed.rowcount} subject(s) from KG 3 not in Basic 1")
        except Exception as e:
            db.rollback()
            print(f"[sync_kg3_subjects] FAILED on delete step: {e}")
            return

        try:
            added = db.execute(text(INSERT_SQL))
            db.commit()
            print(f"[sync_kg3_subjects] Added {added.rowcount} subject(s) to KG 3 from Basic 1")
        except Exception as e:
            db.rollback()
            print(f"[sync_kg3_subjects] FAILED on insert step: {e}")
            return

        print("[sync_kg3_subjects] Done — KG 3 now mirrors Basic 1.")
    finally:
        db.close()


if __name__ == "__main__":
    # Lets you also run it manually with: python -m app.scripts.sync_kg3_subjects
    sync_kg3_subjects()