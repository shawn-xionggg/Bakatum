"""SQLite persistence for tasks and study sessions."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "syla.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    type TEXT DEFAULT 'other',
    due_date TEXT,
    due_text TEXT,
    description TEXT,
    grade_weight TEXT,
    importance TEXT DEFAULT 'medium',
    estimated_minutes INTEGER DEFAULT 60,
    link TEXT,
    links TEXT DEFAULT '[]',
    subtasks TEXT DEFAULT '[]',
    status TEXT DEFAULT 'todo',          -- todo | in_progress | done
    submitted INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(course, title)
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    planned_minutes INTEGER NOT NULL,
    extended_minutes INTEGER DEFAULT 0,
    started_at TEXT DEFAULT (datetime('now')),
    ended_at TEXT
);
"""


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)


def _row_to_task(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["links"] = json.loads(d["links"] or "[]")
    d["subtasks"] = json.loads(d["subtasks"] or "[]")
    d["submitted"] = bool(d["submitted"])
    return d


def import_tasks(course: str, assignments: list[dict]) -> int:
    """Upsert reviewed assignments. Returns count inserted/updated."""
    n = 0
    with _conn() as c:
        for a in assignments:
            est = a.get("estimated_hours")
            minutes = int(float(est) * 60) if est else 60
            c.execute(
                """INSERT INTO tasks (course, title, type, due_date, due_text, description,
                                     grade_weight, importance, estimated_minutes, link, subtasks)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(course, title) DO UPDATE SET
                       due_date=excluded.due_date, due_text=excluded.due_text,
                       description=excluded.description, grade_weight=excluded.grade_weight,
                       importance=excluded.importance, estimated_minutes=excluded.estimated_minutes,
                       link=COALESCE(excluded.link, tasks.link)""",
                (
                    course,
                    a.get("title", "Untitled"),
                    a.get("type", "other"),
                    a.get("due_date"),
                    a.get("due_text"),
                    a.get("description"),
                    a.get("grade_weight"),
                    a.get("importance", "medium"),
                    minutes,
                    a.get("link"),
                    json.dumps(a.get("subtasks", [])),
                ),
            )
            n += 1
    return n


def list_tasks(course: str | None = None, status: str | None = None) -> list[dict]:
    q, params = "SELECT * FROM tasks WHERE 1=1", []
    if course:
        q += " AND course = ?"
        params.append(course)
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY due_date IS NULL, due_date, id"
    with _conn() as c:
        return [_row_to_task(r) for r in c.execute(q, params)]


def get_task(task_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(r) if r else None


def update_task(task_id: int, fields: dict) -> dict | None:
    allowed = {
        "title", "type", "due_date", "due_text", "description", "grade_weight",
        "importance", "estimated_minutes", "link", "status", "submitted", "course",
    }
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            params.append(v)
        elif k in ("links", "subtasks"):
            sets.append(f"{k} = ?")
            params.append(json.dumps(v))
    if sets:
        params.append(task_id)
        with _conn() as c:
            c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
    return get_task(task_id)


def list_courses() -> list[str]:
    with _conn() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT course FROM tasks ORDER BY course")]


# ---------------- sessions ----------------

def start_session(task_id: int, planned_minutes: int) -> dict:
    with _conn() as c:
        c.execute("UPDATE sessions SET ended_at = datetime('now') WHERE ended_at IS NULL")
        cur = c.execute(
            "INSERT INTO sessions (task_id, planned_minutes) VALUES (?, ?)",
            (task_id, planned_minutes),
        )
        c.execute("UPDATE tasks SET status = 'in_progress' WHERE id = ?", (task_id,))
        return {"id": cur.lastrowid, "task_id": task_id, "planned_minutes": planned_minutes, "extended_minutes": 0}


def active_session() -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC").fetchone()
        return dict(r) if r else None


def extend_session(session_id: int, minutes: int) -> dict | None:
    with _conn() as c:
        c.execute(
            "UPDATE sessions SET extended_minutes = extended_minutes + ? WHERE id = ? AND ended_at IS NULL",
            (minutes, session_id),
        )
        r = c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(r) if r else None


def finish_session(session_id: int) -> dict | None:
    with _conn() as c:
        c.execute("UPDATE sessions SET ended_at = datetime('now') WHERE id = ?", (session_id,))
        r = c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(r) if r else None


def progress_counts() -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        done = c.execute("SELECT COUNT(*) FROM tasks WHERE status = 'done'").fetchone()[0]
        return {"total": total, "done": done}
