"""
Montessori / Early-Years report data — Jaasiel Education Centre.

Creche, Daycare, Pre-Nursery and KG classes are assessed differently
from Basic/JSS/SS classes: instead of subject scores + average + grade
+ position, each pupil is rated 1-3 on a fixed list of developmental
skills/behaviours, grouped into categories. This mirrors the two
paper forms currently in use ("My Montessori School" report + "School
Management Report").

Edit MONTESSORI_CLASSES below to control exactly which class names use
this report instead of the normal subject-based one.
"""

# ── Which classes use the Montessori report instead of subjects ──
# Must match the class name exactly as stored in the `classes` table /
# SCHOOL_CLASSES (case-insensitive compare is used everywhere below).
MONTESSORI_CLASSES = [
    "Creche",
    "Daycare",
    "Pre-Nursery",
    "KG 1",
    "KG 2",
    "KG 3",
]


def is_montessori_class(class_name: str | None) -> bool:
    if not class_name:
        return False
    return class_name.strip().lower() in {c.lower() for c in MONTESSORI_CLASSES}


# ── Rating scale (from the "Grading Key" on the paper form) ──────
GRADING_KEY = {
    1: "Working towards the level expected",
    2: "Working within the level expected",
    3: "Working beyond the level expected",
}

# ── Categories & skill items, in display order ────────────────────
MONTESSORI_CATEGORIES: dict[str, list[str]] = {
    "Music and Physical Education": [
        "Shows interest in musical and dance activities",
        "Participates in physical activities",
        "Understands and follows the rules of the game",
        "Adjustment to school",
        "Shows self confidence",
    ],
    "Social Emotional Development": [
        "Participates in group activities",
        "Is considerate and respectful of others",
        "Maintains focus during activities",
        "Interacts with peers",
    ],
    "Work Habits": [
        "Listens attentively",
        "Follows simple directions",
        "Handles materials carefully",
        "Watches a presentation with concentration",
        "Completes an activity",
        "Replaces apparatus after use",
    ],
    "Communication, Language & Literacy": [
        "Ability to comprehend the language",
        "Ability to express thoughts and feelings",
        "Identifies phonics",
        "Identifies lower case letters",
        "Shows interest in books/stories",
        "Can identify common fruits, vegetables, seasons, flowers, colours, "
        "transportation, animals, their food and homes",
    ],
    "Gross and Fine Motor Skills": [
        "Can run, jump, climb, walk on the line, catch and throw a ball",
        "Holds pencil correctly",
        "Shows interest in writing/colouring",
    ],
    "Development of Creative Expression": [
        "Recognises the following geometric shapes: Circle, Oval, Triangle, "
        "Rectangle, Heart, Diamond, Square & Star",
    ],
    "Hand and Eye Co-ordination": [
        "Is able to pour beans from jug to jug",
        "Spooning beads from bowl to bowl",
        "Large button frames",
        "Small button frames",
        "Sponging",
    ],
    "Mathematical Development": [
        "Identifies numerals (0-10)",
        "Is able to count objects (0-10)",
        "Counts by rote",
        "Sorts objects by colour",
        "Sorts objects by shape",
        "Pairs sets of picture cards",
        "Can make the following comparisons: Big-small; long-short; empty-full; "
        "hot-cold; sweet-sour-salty",
        "Is able to sort and classify",
        "Can interpret the following spatial positions: in-out; up-down; "
        "over-under; less than-more than",
        "Can arrange more than two objects from Big-small; Long-short",
    ],
    "Writing Readiness": [
        "Writing Patterns",
        "Writing Skills",
        "Scribbling Skills",
    ],
}


def blank_ratings() -> dict:
    """A ratings dict with every item defaulted to None (unrated)."""
    return {cat: {item: None for item in items} for cat, items in MONTESSORI_CATEGORIES.items()}


def validate_ratings(ratings: dict) -> dict:
    """
    Clean incoming ratings: only keep known categories/items, coerce
    values to int 1-3 or None. Unknown keys are dropped silently.
    """
    cleaned = {}
    ratings = ratings or {}
    for cat, items in MONTESSORI_CATEGORIES.items():
        incoming_cat = ratings.get(cat, {}) or {}
        cleaned[cat] = {}
        for item in items:
            v = incoming_cat.get(item)
            if v is None or v == "":
                cleaned[cat][item] = None
            else:
                try:
                    iv = int(v)
                    cleaned[cat][item] = iv if iv in (1, 2, 3) else None
                except (TypeError, ValueError):
                    cleaned[cat][item] = None
    return cleaned