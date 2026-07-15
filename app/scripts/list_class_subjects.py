"""
app/scripts/list_class_subjects.py

READ-ONLY diagnostic. Does not insert, update, or delete anything.

Prints out exactly which subjects are currently linked to each class
in the database (via the class_subjects table), so we can see the
real state instead of guessing from the admin UI.

WHERE THIS FILE GOES
---------------------
Put it at:  app/scripts/list_class_subjects.py

HOW TO WIRE IT IN (temporary — for one run)
---------------------------------------------
In main.py, add the import:

    from app.scripts.list_class_subjects import list_class_subjects

And call it inside your startup event:

    @app.on_event("startup")
    def _startup_data_fixes():
        run_class_subject_fixes()
        list_class_subjects()

Push, redeploy, check the logs for "[list_class_subjects]" lines,
then remove this call + import again since it's just for diagnosis.
"""

from sqlalchemy import text
from app.db.base import SessionLocal


QUERY = """
SELECT c.name AS class_name, s.name AS subject_name
FROM classes c
LEFT JOIN class_subjects cs ON cs.class_id = c.id
LEFT JOIN subjects s ON s.id = cs.subject_id
WHERE c.name IN ('Basic 1', 'KG 3', 'KG 1', 'KG 2', 'Jss 1')
ORDER BY c.name, s.name
"""

ALL_CLASSES_QUERY = "SELECT id, name FROM classes ORDER BY name"


def list_class_subjects():
    db = SessionLocal()
    try:
        print("[list_class_subjects] All classes currently in the database:")
        for row in db.execute(text(ALL_CLASSES_QUERY)):
            print(f"[list_class_subjects]   id={row[0]}  name={row[1]!r}")

        print("[list_class_subjects] Subjects currently linked per class:")
        rows = db.execute(text(QUERY)).fetchall()
        current_class = None
        for class_name, subject_name in rows:
            if class_name != current_class:
                current_class = class_name
                print(f"[list_class_subjects] {class_name}:")
            print(f"[list_class_subjects]   - {subject_name}")
        print("[list_class_subjects] Done.")
    finally:
        db.close()


if __name__ == "__main__":
    list_class_subjects()