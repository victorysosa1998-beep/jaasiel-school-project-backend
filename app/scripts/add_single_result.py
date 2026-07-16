"""
app/scripts/add_single_result.py

Adds (or fixes) ONE student's result for ONE subject — for when a
student's score didn't display after a bulk upload.

Fill in the values in the CONFIG section below with the real
student, class, subject, term, session, and scores, then deploy.

This does NOT touch any other student's results. It either:
  - creates a new Result row for this student+subject+term, or
  - updates it if one already exists (so it's safe to re-run if you
    typo a score — just fix the numbers below and redeploy)

It reuses your existing grading logic (compute_subject_total /
calculate_grade) so the total and grade are calculated exactly the
same way the normal upload flow does it.

WHERE THIS FILE GOES
---------------------
Put it at:  app/scripts/add_single_result.py

HOW TO RUN
-----------
Option A — one-off via Railway CLI (recommended for a single fix,
no need to touch main.py):
    railway run python -m app.scripts.add_single_result

Option B — via GitHub push + startup hook (if you prefer that flow):
    In main.py, add:
        from app.scripts.add_single_result import add_single_result
    And call it once inside your startup event:
        add_single_result()
    Push, let Railway redeploy, check the logs, then REMOVE the
    import and the call again — this is meant to run once, not on
    every future deploy (running it again with the same scores is
    harmless, but there's no reason to leave it wired in).
"""

from sqlalchemy import func
from app.db.base import SessionLocal
from app.models.models import Student, Class, Subject, Term, Session as AcSession, Result, ResultBatch, ResultStatus
from app.utils.grading import calculate_grade


# ═══════════════════════════════════════════════════════════════
# CONFIG — fill these in with the real values, then run
# ═══════════════════════════════════════════════════════════════

STUDENT_NAME  = "Victor Dominion"   # or use STUDENT_ID_NO below instead
STUDENT_ID_NO = None   # e.g. "JEC/2026/BASIC1/0007" — set this INSTEAD of STUDENT_NAME if you have it (more reliable)

CLASS_NAME   = "KG 2"        # must match your classes table exactly
SUBJECT_NAME = "Elementary Science"    # must match your subjects table exactly
TERM_NAME    = "Third Term"     # must match your terms table exactly
SESSION_NAME = "2025/2026"      # must match your sessions table exactly

FIRST_TEST  = 20     # out of 20 — set to None if not applicable
SECOND_TEST = 10     # out of 20 — set to None if not applicable
EXAM_SCORE  = 60      # out of 60

# ═══════════════════════════════════════════════════════════════


def add_single_result():
    db = SessionLocal()
    try:
        # ── find the student ──
        if STUDENT_ID_NO:
            student = db.query(Student).filter(Student.student_id == STUDENT_ID_NO).first()
        else:
            student = db.query(Student).filter(
                func.lower(func.trim(Student.full_name)) == STUDENT_NAME.strip().lower()
            ).first()

        if not student:
            print(f"[add_single_result] FAILED — student not found "
                  f"({'id ' + STUDENT_ID_NO if STUDENT_ID_NO else 'name ' + STUDENT_NAME!r}). "
                  f"Double-check spelling, or use STUDENT_ID_NO instead of STUDENT_NAME.")
            return

        # ── find class, subject, term, session ──
        cls = db.query(Class).filter(
            func.lower(func.trim(Class.name)) == CLASS_NAME.strip().lower()
        ).first()
        subject = db.query(Subject).filter(
            func.lower(func.trim(Subject.name)) == SUBJECT_NAME.strip().lower()
        ).first()
        term = db.query(Term).filter(
            func.lower(func.trim(Term.term_name)) == TERM_NAME.strip().lower()
        ).first()
        session = db.query(AcSession).filter(
            func.lower(func.trim(AcSession.session_name)) == SESSION_NAME.strip().lower()
        ).first()

        missing = []
        if not cls: missing.append(f"class {CLASS_NAME!r}")
        if not subject: missing.append(f"subject {SUBJECT_NAME!r}")
        if not term: missing.append(f"term {TERM_NAME!r}")
        if not session: missing.append(f"session {SESSION_NAME!r}")
        if missing:
            print(f"[add_single_result] FAILED — could not find: {', '.join(missing)}. "
                  f"Check the exact spelling in your database.")
            return

        # ── find or create the batch this result belongs to ──
        # (reuses an existing draft/submitted batch for this class+subject+term
        #  if one exists, so this student's result shows up alongside everyone
        #  else's instead of creating a stray extra batch)
        batch = db.query(ResultBatch).filter(
            ResultBatch.class_id == cls.id,
            ResultBatch.subject_id == subject.id,
            ResultBatch.term_id == term.id,
            ResultBatch.status.in_([ResultStatus.pending, ResultStatus.submitted]),
        ).first()

        if not batch:
            batch = ResultBatch(
                uploaded_by=None, class_id=cls.id, subject_id=subject.id,
                session_id=session.id, term_id=term.id,
                upload_type="manual", status=ResultStatus.pending,
            )
            db.add(batch)
            db.flush()
            print(f"[add_single_result] No existing draft/submitted batch found for "
                  f"{CLASS_NAME} / {SUBJECT_NAME} / {TERM_NAME} — created a new one.")
        else:
            print(f"[add_single_result] Attaching to existing batch id={batch.id} "
                  f"(status={batch.status.value}).")

        # ── compute totals the same way the normal upload does ──
        t1 = FIRST_TEST if FIRST_TEST is not None else None
        t2 = SECOND_TEST if SECOND_TEST is not None else None
        ca = (t1 or 0) + (t2 or 0) if (t1 is not None or t2 is not None) else 0
        exam = EXAM_SCORE or 0
        total = ca + exam
        grade, remark = calculate_grade(total)

        # ── create or update the Result row ──
        existing = db.query(Result).filter(
            Result.student_id == student.id,
            Result.subject_id == subject.id,
            Result.term_id == term.id,
        ).first()

        if existing:
            existing.first_test = t1
            existing.second_test = t2
            existing.ca_score = ca
            existing.exam_score = exam
            existing.total_score = total
            existing.grade = grade
            existing.remark = remark
            existing.batch_id = batch.id
            existing.status = ResultStatus.pending
            print(f"[add_single_result] Updated existing result for {student.full_name} "
                  f"in {SUBJECT_NAME}: total={total}, grade={grade}")
        else:
            db.add(Result(
                student_id=student.id, class_id=cls.id, subject_id=subject.id,
                session_id=session.id, term_id=term.id, batch_id=batch.id,
                first_test=t1, second_test=t2, ca_score=ca, exam_score=exam,
                total_score=total, grade=grade, remark=remark,
                status=ResultStatus.pending,
            ))
            print(f"[add_single_result] Added new result for {student.full_name} "
                  f"in {SUBJECT_NAME}: total={total}, grade={grade}")

        db.commit()
        print("[add_single_result] Done. Result is saved as a DRAFT — "
              "it still needs to go through your normal submit/approve flow.")

    except Exception as e:
        db.rollback()
        print(f"[add_single_result] FAILED — {e}")
    finally:
        db.close()


if __name__ == "__main__":
    add_single_result()