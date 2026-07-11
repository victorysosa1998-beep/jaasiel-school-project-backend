from sqlalchemy.orm import sessionmaker
from app.db.base import Base, engine
from app.models.models import Student
from app.core.security import hash_password

SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

updated = 0
skipped = 0

students = db.query(Student).all()
print(f"Found {len(students)} students. Starting reset...")

for i, student in enumerate(students, start=1):
    if student.date_of_birth is None:
        print(f"Skipping {student.full_name} — no DOB on file")
        skipped += 1
        continue

    # Default password = DDMMYY, e.g. 22 Jan 2012 -> 220112
    default_password = student.date_of_birth.strftime("%d%m%y")

    student.password_hash = hash_password(default_password)
    student.must_change_pwd = True
    updated += 1

    if i % 50 == 0:
        print(f"...processed {i}/{len(students)}")

db.commit()
db.close()

print("=" * 50)
print(f"Passwords reset: {updated}")
print(f"Skipped (no DOB): {skipped}")
print("=" * 50)