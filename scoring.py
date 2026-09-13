"""Deterministic urgency + priority scoring applied on top of Claude's extraction."""

from datetime import date, datetime

URGENCY_ORDER = {"overdue": 0, "critical": 1, "high": 2, "medium": 3, "low": 4, "unscheduled": 5}
IMPORTANCE_SCORE = {"high": 3, "medium": 2, "low": 1}


def parse_due_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def urgency_for(due: date | None, today: date | None = None) -> str:
    if due is None:
        return "unscheduled"
    today = today or date.today()
    delta = (due - today).days
    if delta < 0:
        return "overdue"
    if delta <= 2:
        return "critical"
    if delta <= 7:
        return "high"
    if delta <= 14:
        return "medium"
    return "low"


def enrich_assignment(a: dict, window_start: date, window_end: date, today: date | None = None) -> dict:
    """Add computed fields: parsed due date, urgency, in-window flag, priority score."""
    due = parse_due_date(a.get("due_date"))
    urgency = urgency_for(due, today)
    a["due_date"] = due.isoformat() if due else None
    a["urgency"] = urgency
    a["in_window"] = due is None or window_start <= due <= window_end
    a["importance"] = a.get("importance") if a.get("importance") in IMPORTANCE_SCORE else "medium"
    # Lower = more pressing. Urgency dominates; importance breaks ties.
    a["priority"] = URGENCY_ORDER[urgency] * 10 - IMPORTANCE_SCORE[a["importance"]]
    return a
