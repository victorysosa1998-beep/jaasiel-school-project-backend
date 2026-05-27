"""
Nigerian grading system utilities — Jaasiel Education Centre.

Scoring rules (from Individual Student Assessment Form):
  Subject Total  = 1st Test (20) + 2nd Test (20) + Exam (60) = 100
  Overall Total  = sum of all scored subject totals
                   (subjects with no scores are EXCLUDED)
  Average %      = Overall Total ÷ Number of Scored Subjects
                   e.g. 1010 ÷ 14 = 72.1%
                   NOT (Total ÷ Max Obtainable) × 100

Grading scale (from report card keys):
  70–100 = A    Distinction
  60–69  = B    Credit
  50–59  = C    Good
  45–49  = D    Fair
  40–44  = E    Pass
   0–39  = F    Fail
"""


def calculate_grade(score: float | None) -> tuple[str, str]:
    """Return (grade, remark) for a subject total score (0–100).

    Returns ('—', '—') when score is None / blank (unentered subject).
    """
    if score is None:
        return "—", "—"
    s = float(score)
    if s == 100: return "A+", "Distinction"
    if s >= 70:  return "A",  "Distinction"
    if s >= 60:  return "B",  "Credit"
    if s >= 50:  return "C",  "Good"
    if s >= 45:  return "D",  "Fair"
    if s >= 40:  return "E",  "Pass"
    return "F", "Fail"


def compute_subject_total(
    first_test:  float | None,
    second_test: float | None,
    ca_score:    float | None,
    exam_score:  float | None,
    total_score: float | None,
) -> float | None:
    """
    Calculate a subject's total score from its components.

    Priority order:
    1. If total_score is already stored, return it as-is.
    2. If exam_score is present:
       a. If first_test or second_test present → total = t1 + t2 + exam
       b. If ca_score present → total = ca + exam
       c. Else → total = exam
    3. If nothing useful → return None (subject not attempted, exclude it).
    """
    if total_score is not None:
        return float(total_score)

    if exam_score is None:
        return None  # Exclude: no exam score means subject not attempted

    exam = float(exam_score)

    if first_test is not None or second_test is not None:
        t1 = float(first_test)  if first_test  is not None else 0.0
        t2 = float(second_test) if second_test is not None else 0.0
        return t1 + t2 + exam

    if ca_score is not None:
        return float(ca_score) + exam

    return exam  # Only exam provided


def calculate_averages(results: list[dict]) -> dict:
    """
    Given a list of result dicts (one per subject per student),
    compute the school's statistics:

    Returns:
        {
          "scored":        int,   # subjects actually attempted
          "overall_total": float, # sum of subject totals
          "obtainable":    int,   # scored × 100
          "average":       float, # overall_total ÷ scored  (Jaasiel formula)
          "grade":         str,
          "remark":        str,
        }
    """
    totals = []
    for r in results:
        t = compute_subject_total(
            r.get("first_test"),
            r.get("second_test"),
            r.get("ca_score"),
            r.get("exam_score"),
            r.get("total_score"),
        )
        if t is not None:
            totals.append(t)

    scored        = len(totals)
    overall_total = sum(totals)
    obtainable    = scored * 100
    average       = overall_total / scored if scored else 0.0
    grade, remark = calculate_grade(average)

    return {
        "scored":        scored,
        "overall_total": round(overall_total, 2),
        "obtainable":    obtainable,
        "average":       round(average, 2),
        "grade":         grade,
        "remark":        remark,
    }


def generate_student_id(year: int, class_name: str, sequence: int) -> str:
    clean = class_name.upper().replace(" ", "")[:6]
    return f"JEC/{year}/{clean}/{sequence:04d}"


def generate_username(first: str, middle: str, last: str, dob) -> str:
    """Generate username: firstmiddlelast + last 2 digits of birth year."""
    year2 = str(dob.year)[-2:] if dob else ""
    name  = (first + middle + last).lower().replace(" ", "")
    return name + year2


def generate_default_password(dob) -> str:
    """Generate default password: DDMMYY from date of birth."""
    if not dob:
        return "123456"
    return f"{dob.day:02d}{dob.month:02d}{str(dob.year)[-2:]}"


def fuzzy_match_name(extracted: str, registered: str) -> float:
    """Return similarity ratio between 0 and 1."""
    try:
        from fuzzywuzzy import fuzz
        return fuzz.token_sort_ratio(extracted.lower(), registered.lower()) / 100.0
    except ImportError:
        a = set(extracted.lower().split())
        b = set(registered.lower().split())
        if not a or not b:
            return 0.0
        return len(a & b) / max(len(a), len(b))