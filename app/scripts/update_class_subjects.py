"""
app/scripts/update_class_subjects.py

One-time subject-list update for Jaasiel RMS.

WHAT IT DOES
  Replaces the subject list (class_subjects links) for:
    - Jss 1, Jss 2, Jss 3
    - SS 1, SS 2                       (SS 3 is NOT touched)
    - KG 3, Basic 1, Basic 2           (Primary 1-2)
    - Basic 3, Basic 4, Basic 5        (Primary 3-5)

WHAT IT NEVER TOUCHES
  - results / result_batches   (old results keep their original subject_id,
                                so every past term still shows the subjects
                                it was recorded with)
  - the subjects table rows    (nothing is renamed or deleted; new subjects
                                are created as NEW rows, existing rows with
                                an EXACTLY matching name are reused)
  - any class not listed above (KG 1, KG 2, SS 3, Creche, etc.)

THE ONLY REMOVALS
  Old class_subjects LINKS for the classes above that are not in the new
  list. Links are added first, then removed, in one transaction. Every
  removed link is printed and saved (JSON) in school_settings under the key
  "subject_update_2026_10_removed_links" so it can be restored.

HOW TO RUN
  Preview only (changes nothing):
      python -m app.scripts.update_class_subjects
  Apply:
      python -m app.scripts.update_class_subjects --apply

  Or on Railway: set the variable APPLY_SUBJECT_UPDATE=1 and redeploy
  (see main.py). It applies once, records a marker in school_settings,
  and skips on every later deploy. Remove the variable afterwards.
  Use --force to re-run after the marker exists.

DO THIS AFTER the current term's results are published/locked for these
classes, so no in-progress term ends up with a mix of old and new subjects.
"""
import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import func

from app.db.base import SessionLocal
from app.models.models import (
    Class, Subject, ClassSubject, Result, ResultBatch, SchoolSettings,
)

MARKER_KEY  = "subject_update_2026_10_applied"
BACKUP_KEY  = "subject_update_2026_10_removed_links"

JSS = [
    "English Studies", "Mathematics", "Nigerian Language (Edo)",
    "Intermediate Science", "Physical and Health Education",
    "Digital Technologies", "Christian Religion Studies", "Nigerian History",
    "Social and Citizenship Studies", "Cultural and Creative Art",
    "Livestock Farming", "Business Studies",
]

SS_1_2 = [
    "English Studies", "Mathematics", "Livestock Farming",
    "Citizenship and Heritage Studies", "Digital Technologies", "Biology",
    "Chemistry", "Physics", "Agricultural Science", "Food and Nutrition",
    "Nigerian History", "Government", "Christian Religion Studies",
    "Marketing", "Economics",
]

KG3_TO_PRIMARY_2 = [
    "English Studies", "Mathematics", "Nigerian Language (Edo)",
    "Basic Science", "Physical and Health Education",
    "Christian Religion Studies", "Nigerian History",
    "Social and Citizenship Studies", "Cultural and Creative Art",
    "Verbal Reasoning", "Quantitative Reasoning", "Handwriting",
]

PRIMARY_3_TO_5 = [
    "English Studies", "Mathematics", "Nigerian Language (Edo)",
    "Basic Science and Technology", "Physical and Health Education",
    "Basic Digital Literacy", "Christian Religion Studies",
    "Nigerian History", "Social Studies and Citizenship Education",
    "Cultural and Creative Art", "Prevocational Studies",
    "Quantitative Reasoning", "Verbal Reasoning", "Handwriting",
]

# label -> (possible class names, lower-case compared, new subject list)
# "Primary N" is stored as "Basic N" in this database; both are matched.
CLASS_PLAN = [
    ("Jss 1",  ["jss 1"],              JSS),
    ("Jss 2",  ["jss 2"],              JSS),
    ("Jss 3",  ["jss 3"],              JSS),
    ("SS 1",   ["ss 1"],               SS_1_2),
    ("SS 2",   ["ss 2"],               SS_1_2),
    ("KG 3",   ["kg 3"],               KG3_TO_PRIMARY_2),
    ("Basic 1 (Primary 1)", ["basic 1", "primary 1"], KG3_TO_PRIMARY_2),
    ("Basic 2 (Primary 2)", ["basic 2", "primary 2"], KG3_TO_PRIMARY_2),
    ("Basic 3 (Primary 3)", ["basic 3", "primary 3"], PRIMARY_3_TO_5),
    ("Basic 4 (Primary 4)", ["basic 4", "primary 4"], PRIMARY_3_TO_5),
    ("Basic 5 (Primary 5)", ["basic 5", "primary 5"], PRIMARY_3_TO_5),
]


def _log(msg):
    print(f"[update_class_subjects] {msg}")


def _find_subject(db, name):
    """Exact-name match only (so 'English studies' is NOT reused for
    'English Studies' — that would rename old results). Prefers an
    active row."""
    return (
        db.query(Subject)
        .filter(Subject.name == name)
        .order_by(Subject.is_active.desc(), Subject.id.asc())
        .first()
    )


def _find_classes(db, candidates):
    return (
        db.query(Class)
        .filter(func.lower(func.trim(Class.name)).in_(candidates))
        .all()
    )


def run(apply=False, force=False):
    db = SessionLocal()
    try:
        if apply and not force:
            if db.query(SchoolSettings).filter(SchoolSettings.key == MARKER_KEY).first():
                _log("Already applied earlier (marker found) — skipping. Use --force to re-run.")
                return

        mode = "APPLY" if apply else "PREVIEW (nothing will be changed)"
        _log(f"Mode: {mode}")

        results_before = db.query(Result).count()
        batches_before = db.query(ResultBatch).count()

        # ── Resolve subjects (reuse exact matches, create the rest) ──
        all_names = []
        for _, _, names in CLASS_PLAN:
            for n in names:
                if n not in all_names:
                    all_names.append(n)

        subject_ids = {}      # name -> id (None while previewing a new one)
        created, reactivated = [], []
        for name in all_names:
            s = _find_subject(db, name)
            if s is None:
                created.append(name)
                if apply:
                    s = Subject(name=name, is_active=True)
                    db.add(s)
                    db.flush()
                    subject_ids[name] = s.id
                else:
                    subject_ids[name] = None
            else:
                subject_ids[name] = s.id
                if not s.is_active:
                    reactivated.append(name)
                    if apply:
                        s.is_active = True

        _log(f"New subject rows to create ({len(created)}): {created or 'none'}")
        _log(f"Existing inactive subjects to reactivate ({len(reactivated)}): {reactivated or 'none'}")

        # ── Per class: add new links, then remove old ones ──
        removed_backup = []
        for label, candidates, names in CLASS_PLAN:
            classes = _find_classes(db, candidates)
            if not classes:
                _log(f"{label}: class NOT FOUND in database — skipped")
                continue
            target_ids = {subject_ids[n] for n in names if subject_ids[n] is not None}
            for cls in classes:
                links = db.query(ClassSubject).filter(ClassSubject.class_id == cls.id).all()
                current_ids = {l.subject_id for l in links}
                to_add_ids = target_ids - current_ids
                new_names = [n for n in names if subject_ids[n] is None]
                add_names = sorted(
                    [n for n in names if subject_ids[n] in to_add_ids] + new_names
                )
                stale = [l for l in links if l.subject_id not in target_ids]
                remove_names = sorted(
                    (l.subject.name if l.subject else f"id={l.subject_id}") for l in stale
                )
                _log(f"{cls.name} (id={cls.id}):")
                _log(f"    add    ({len(add_names)}): {add_names or 'none'}")
                _log(f"    unlink ({len(remove_names)}): {remove_names or 'none'}")

                if apply:
                    for sid in to_add_ids:
                        db.add(ClassSubject(class_id=cls.id, subject_id=sid))
                    db.flush()
                    for l in stale:
                        removed_backup.append({
                            "class_id": cls.id, "class_name": cls.name,
                            "subject_id": l.subject_id,
                            "subject_name": l.subject.name if l.subject else None,
                        })
                        db.delete(l)

        if not apply:
            _log("Preview finished. Re-run with --apply to make these changes.")
            return

        db.flush()

        # ── Safety check: results must be completely untouched ──
        if (db.query(Result).count() != results_before
                or db.query(ResultBatch).count() != batches_before):
            raise RuntimeError("Result/ResultBatch row counts changed — aborting.")

        now = datetime.now(timezone.utc).isoformat()
        for key, value in ((BACKUP_KEY, json.dumps(removed_backup)), (MARKER_KEY, now)):
            row = db.query(SchoolSettings).filter(SchoolSettings.key == key).first()
            if row:
                row.value = value
            else:
                db.add(SchoolSettings(key=key, value=value))

        db.commit()
        _log(f"DONE. {len(removed_backup)} old class-subject link(s) removed "
             f"(saved under school_settings key '{BACKUP_KEY}').")
        _log(f"Results rows before/after: {results_before}/{results_before} — untouched.")
    except Exception as e:
        db.rollback()
        _log(f"FAILED — everything rolled back, nothing changed: {e}")
        raise
    finally:
        db.close()


def run_update_class_subjects():
    """Startup hook (main.py). Does nothing unless the Railway variable
    APPLY_SUBJECT_UPDATE=1 is set; then applies once (marker-guarded)."""
    if os.getenv("APPLY_SUBJECT_UPDATE", "").strip() != "1":
        return
    try:
        run(apply=True)
    except Exception:
        pass  # already logged; never block app startup


if __name__ == "__main__":
    run(apply="--apply" in sys.argv, force="--force" in sys.argv)