"""
app/scripts/publish_kg_batches.py

One-off data fix for Jaasiel RMS, meant to run automatically on
app startup after you push to GitHub and Railway redeploys.

PROBLEM:
  KG 1, KG 2, and KG 3 results show as "not yet published" to
  parents/students even though the individual Result rows have
  status = 'published'. The real gate is ResultBatch.approved_at —
  every batch for these three classes has approved_at = NULL, so
  the app is (correctly) treating them as unapproved.

FIX:
  Sets approved_at = now() and approved_by = <admin user id> on
  every ResultBatch for KG 1 / KG 2 / KG 3 that is still
  approved_at IS NULL.

SAFE TO LEAVE IN / RE-RUN:
  Only touches batches where approved_at IS NULL, so once everything
  is approved this becomes a no-op on every future deploy — nothing
  gets re-approved or overwritten.

WHERE THIS FILE GOES
---------------------
Put it at:  app/scripts/publish_kg_batches.py

HOW TO WIRE IT IN
-------------------
In main.py, add the import:

    from app.scripts.publish_kg_batches import publish_kg_batches

And call it inside your startup event:

    @app.on_event("startup")
    def _startup_data_fixes():
        run_class_subject_fixes()
        publish_kg_batches()  # ONE-OFF -- remove this line after confirming in logs

Push, let Railway redeploy, check the logs for "[publish_kg_batches]"
lines, confirm KG 1/2/3 results now show as published in the app,
then remove the call (and import) from main.py again since there's
no reason to leave a one-off fix wired into every future deploy.
"""

from app.db.base import SessionLocal
from app.models.models import Class, Student, Result, ResultBatch


# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

TARGET_CLASSES = ["KG 1", "KG 2", "KG 3"]   # NOTE: stored WITH a space in classes table
ADMIN_USER_ID = 1                            # Super Administrator (users.id)

# ═══════════════════════════════════════════════════════════════


def publish_kg_batches():
    db = SessionLocal()
    try:
        print(f"[publish_kg_batches] Checking batches for {', '.join(TARGET_CLASSES)}...")

        # Find the distinct unapproved batch IDs belonging to these classes,
        # via results -> students -> classes (batches don't have a direct
        # class_id column pointed at students, so we go through Result).
        batch_ids = (
            db.query(ResultBatch.id)
            .join(Result, Result.batch_id == ResultBatch.id)
            .join(Student, Student.id == Result.student_id)
            .join(Class, Class.id == Student.class_id)
            .filter(Class.name.in_(TARGET_CLASSES))
            .filter(ResultBatch.approved_at.is_(None))
            .distinct()
            .all()
        )
        batch_ids = [b[0] for b in batch_ids]

        if not batch_ids:
            print("[publish_kg_batches] Nothing to do — all KG 1/2/3 batches are already approved.")
            return

        print(f"[publish_kg_batches] Found {len(batch_ids)} unapproved batch(es): {batch_ids}")

        from sqlalchemy import func

        updated = (
            db.query(ResultBatch)
            .filter(ResultBatch.id.in_(batch_ids))
            .update(
                {
                    ResultBatch.approved_at: func.now(),
                    ResultBatch.approved_by: ADMIN_USER_ID,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        print(f"[publish_kg_batches] Approved {updated} batch(es) as admin id={ADMIN_USER_ID}.")

        # Verify
        remaining = (
            db.query(ResultBatch.id)
            .join(Result, Result.batch_id == ResultBatch.id)
            .join(Student, Student.id == Result.student_id)
            .join(Class, Class.id == Student.class_id)
            .filter(Class.name.in_(TARGET_CLASSES))
            .filter(ResultBatch.approved_at.is_(None))
            .count()
        )
        if remaining == 0:
            print("[publish_kg_batches] Verified: all KG 1/2/3 batches are now approved.")
        else:
            print(f"[publish_kg_batches] WARNING — {remaining} batch(es) still unapproved.")

    except Exception as e:
        db.rollback()
        print(f"[publish_kg_batches] FAILED — {e}")
    finally:
        db.close()


if __name__ == "__main__":
    # Lets you also run it manually with: python -m app.scripts.publish_kg_batches
    publish_kg_batches()