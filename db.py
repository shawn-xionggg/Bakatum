"""Supabase (Postgres) persistence for tasks and study sessions.

Tables are created by supabase_schema.sql (run it once in the SQL editor).
Timestamps are normalized back to SQLite-style "YYYY-MM-DD HH:MM:SS" UTC
strings so the frontend's `new Date(started_at + "Z")` keeps working.
"""

import json
import os
from datetime import datetime, timezone

_sb = None


def init_db():
    """Create the Supabase client. Tables are managed by supabase_schema.sql."""
    global _sb
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY must be set in .env "
            "(and supabase_schema.sql must have been run in the SQL editor)."
        )
    _sb = create_client(url, key)


def _tasks():
    return _sb.table("tasks")


def _sessions():
    return _sb.table("sessions")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts(v):
    """timestamptz → 'YYYY-MM-DD HH:MM:SS' UTC (keeps the JS `+ 'Z'` contract)."""
    if not v:
        return v
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return v


def _row_to_task(d) -> dict:
    t = dict(d)
    for k in ("links", "subtasks"):
        v = t.get(k)
        if isinstance(v, str):
            t[k] = json.loads(v or "[]")
        elif v is None:
            t[k] = []
    t["submitted"] = bool(t.get("submitted"))
    if t.get("due_date") == "":
        t["due_date"] = None
    t["created_at"] = _ts(t.get("created_at"))
    return t


def _row_to_session(d) -> dict:
    s = dict(d)
    s["started_at"] = _ts(s.get("started_at"))
    s["ended_at"] = _ts(s.get("ended_at"))
    return s


def import_tasks(course: str, assignments: list[dict]) -> int:
    """Upsert reviewed assignments. Returns count inserted/updated."""
    n = 0
    for a in assignments:
        est = a.get("estimated_hours")
        minutes = int(float(est) * 60) if est else 60
        _sb.rpc(
            "import_task",
            {
                "p_course": course,
                "p_title": a.get("title", "Untitled"),
                "p_type": a.get("type", "other"),
                "p_due_date": a.get("due_date") or None,
                "p_due_text": a.get("due_text"),
                "p_description": a.get("description"),
                "p_grade_weight": a.get("grade_weight"),
                "p_importance": a.get("importance", "medium"),
                "p_estimated_minutes": minutes,
                "p_link": a.get("link"),
                "p_subtasks": a.get("subtasks", []),
            },
        ).execute()
        n += 1
    return n


def list_tasks(course: str | None = None, status: str | None = None) -> list[dict]:
    q = _tasks().select("*")
    if course:
        q = q.eq("course", course)
    if status:
        q = q.eq("status", status)
    tasks = [_row_to_task(r) for r in q.execute().data]
    # ORDER BY due_date IS NULL, due_date, id
    tasks.sort(key=lambda t: (t["due_date"] is None, t["due_date"] or "", t["id"]))
    return tasks


def get_task(task_id: int) -> dict | None:
    rows = _tasks().select("*").eq("id", task_id).limit(1).execute().data
    return _row_to_task(rows[0]) if rows else None


def update_task(task_id: int, fields: dict) -> dict | None:
    allowed = {
        "title", "type", "due_date", "due_text", "description", "grade_weight",
        "importance", "estimated_minutes", "link", "status", "submitted", "course",
    }
    upd = {}
    for k, v in fields.items():
        if k in allowed:
            if k == "due_date" and v == "":
                v = None
            if k == "submitted":
                v = bool(v)
            upd[k] = v
        elif k in ("links", "subtasks"):
            upd[k] = v if isinstance(v, (list, dict)) else json.loads(v or "[]")
    if upd:
        _tasks().update(upd).eq("id", task_id).execute()
    return get_task(task_id)


def list_courses() -> list[str]:
    rows = _tasks().select("course").execute().data
    return sorted({r["course"] for r in rows})


# ---------------- sessions ----------------

def start_session(task_id: int, planned_minutes: int) -> dict:
    # close any open session first (same as the old behaviour)
    _sessions().update({"ended_at": _now_iso()}).is_("ended_at", "null").execute()
    row = (
        _sessions()
        .insert({"task_id": task_id, "planned_minutes": planned_minutes})
        .execute()
        .data[0]
    )
    _tasks().update({"status": "in_progress"}).eq("id", task_id).execute()
    return {
        "id": row["id"],
        "task_id": task_id,
        "planned_minutes": planned_minutes,
        "extended_minutes": 0,
    }


def active_session() -> dict | None:
    rows = (
        _sessions()
        .select("*")
        .is_("ended_at", "null")
        .order("id", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return _row_to_session(rows[0]) if rows else None


def extend_session(session_id: int, minutes: int) -> dict | None:
    rows = _sb.rpc("extend_session", {"sid": session_id, "mins": minutes}).execute().data
    return _row_to_session(rows[0]) if rows else None


def finish_session(session_id: int) -> dict | None:
    _sessions().update({"ended_at": _now_iso()}).eq("id", session_id).execute()
    rows = _sessions().select("*").eq("id", session_id).limit(1).execute().data
    return _row_to_session(rows[0]) if rows else None


def progress_counts() -> dict:
    total = _tasks().select("id", count="exact", head=True).execute().count
    done = (
        _tasks()
        .select("id", count="exact", head=True)
        .eq("status", "done")
        .execute()
        .count
    )
    return {"total": total or 0, "done": done or 0}
