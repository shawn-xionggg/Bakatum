import os
from datetime import date, datetime, timedelta
from pathlib import Path

import db
from dotenv import load_dotenv, set_key as dotenv_set_key
from flask import Flask, jsonify, render_template, request

load_dotenv()
db.init_db()

from extraction import extract_assignments
import learn
from parsing import extract_text
from research import get_job, resume_job, start_research_job
from scoring import IMPORTANCE_SCORE, URGENCY_ORDER, enrich_assignment, parse_due_date, urgency_for

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB uploads
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # dev: never serve stale JS/CSS

HARDWARE = {"minutes": 30, "skip_ids": set()}  # ESP32-facing state


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return fallback


def _enrich_task(t: dict, today: date | None = None) -> dict:
    """Add live-computed fields to a DB task row."""
    due = parse_due_date(t.get("due_date"))
    t["urgency"] = urgency_for(due, today)
    subs = t.get("subtasks") or []
    if subs:
        done = sum(1 for s in subs if s.get("done"))
        t["remaining_minutes"] = max(15, int(t["estimated_minutes"] * (1 - done / len(subs))))
    else:
        t["remaining_minutes"] = t["estimated_minutes"]
    return t


def _recommend(minutes: int, exclude_ids: set | None = None) -> dict | None:
    """Pick the best task for the available time, with an explanation."""
    today = date.today()
    candidates = [
        _enrich_task(t, today)
        for t in db.list_tasks()
        if t["status"] != "done" and not t["submitted"] and t["id"] not in (exclude_ids or set())
    ]
    if not candidates:
        return None

    def score(t):
        s = URGENCY_ORDER[t["urgency"]] * 100 - IMPORTANCE_SCORE.get(t["importance"], 2) * 10
        if t["remaining_minutes"] <= minutes:
            s -= 25  # fits in the available time
        return s

    pick = min(candidates, key=score)
    reasons = []
    if pick["urgency"] in ("overdue", "critical", "high"):
        reasons.append(f"due {pick['due_date'] or 'soon'} ({pick['urgency']})")
    if pick["importance"] == "high":
        reasons.append("high grade stake")
    if pick["remaining_minutes"] <= minutes:
        reasons.append(f"fits in your {minutes} min (~{pick['remaining_minutes']} min left)")
    else:
        reasons.append(f"~{pick['remaining_minutes']} min left — you'll need more than {minutes} min")
    return {"task": pick, "reason": "; ".join(reasons) or "highest priority remaining"}


@app.get("/")
def index():
    return render_template("landing.html")


@app.get("/app")
def app_page():
    return render_template("index.html")


# ---------------- config ----------------

@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "steel_key": bool(os.environ.get("STEEL_API_KEY")),
        }
    )


@app.get("/api/config")
def get_config():
    return jsonify(
        {
            "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "steel_key": bool(os.environ.get("STEEL_API_KEY")),
        }
    )


@app.post("/api/config")
def set_config():
    """Save API keys to .env and into the running process (localhost dev app)."""
    body = request.get_json(silent=True) or {}
    env_path = Path(".env")
    env_path.touch(exist_ok=True)

    for field, env_name in (
        ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        ("steel_api_key", "STEEL_API_KEY"),
    ):
        value = (body.get(field) or "").strip()
        if value:
            dotenv_set_key(str(env_path), env_name, value)
            os.environ[env_name] = value

    return get_config()


# ---------------- syllabus import ----------------

@app.post("/api/preview")
def preview():
    """Extract text only — lets the user review it before anything goes to the AI."""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded."}), 400
    try:
        text = extract_text(file.filename, file.read())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"text": text})


@app.post("/api/upload")
def upload():
    """Extract assignments. Accepts a file upload OR reviewed text as JSON."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY is not set. Add it in the API keys panel."}), 503

    today = date.today()

    if request.is_json:
        body = request.get_json()
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "No syllabus text provided."}), 400
        window_start = _parse_date(body.get("start_date"), today)
        window_end = _parse_date(body.get("end_date"), today + timedelta(days=14))
    else:
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "No file uploaded."}), 400
        window_start = _parse_date(request.form.get("start_date"), today)
        window_end = _parse_date(request.form.get("end_date"), today + timedelta(days=14))
        try:
            text = extract_text(file.filename, file.read())
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    if window_end < window_start:
        return jsonify({"error": "End date must be on or after start date."}), 400

    try:
        result = extract_assignments(text, window_start, window_end)
    except Exception as e:
        return jsonify({"error": f"Extraction failed: {e}"}), 502

    assignments = [enrich_assignment(a, window_start, window_end, today) for a in result["assignments"]]
    assignments.sort(key=lambda a: a["priority"])

    actionable = [a for a in assignments if a.get("type") != "participation"]
    picks = ([a for a in actionable if a["in_window"]] or actionable)[:3]
    recommended_ids = {id(a) for a in picks}
    for a in assignments:
        a["recommended"] = id(a) in recommended_ids

    return jsonify(
        {
            "course_name": result["course_name"],
            "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
            "assignments": assignments,
        }
    )


# ---------------- tasks ----------------

@app.post("/api/tasks/import")
def tasks_import():
    body = request.get_json(silent=True) or {}
    course = (body.get("course_name") or "").strip()
    assignments = body.get("assignments") or []
    if not assignments:
        return jsonify({"error": "No assignments to import."}), 400
    n = 0
    for a in assignments:
        n += db.import_tasks((a.get("course") or course or "").strip(), [a])
    return jsonify({"imported": n, "course": course})


@app.get("/api/tasks")
def tasks_list():
    tasks = db.list_tasks(course=request.args.get("course"), status=request.args.get("status"))
    today = date.today()
    window_start = _parse_date(request.args.get("start"), today)
    window_end = _parse_date(request.args.get("end"), today + timedelta(days=365))
    out = []
    for t in tasks:
        _enrich_task(t, today)
        due = parse_due_date(t.get("due_date"))
        t["in_window"] = due is None or window_start <= due <= window_end
        out.append(t)
    return jsonify({"tasks": out, "courses": db.list_courses(), "progress": db.progress_counts()})


@app.patch("/api/tasks/<int:task_id>")
def task_update(task_id):
    body = request.get_json(silent=True) or {}
    task = db.update_task(task_id, body)
    if task is None:
        return jsonify({"error": "Task not found."}), 404
    return jsonify(_enrich_task(task))


# ---------------- recommend / sessions ----------------

@app.get("/api/recommend")
def recommend():
    minutes = int(request.args.get("minutes") or HARDWARE["minutes"])
    rec = _recommend(minutes, exclude_ids=HARDWARE["skip_ids"])
    if rec is None:
        return jsonify({"task": None, "reason": "Nothing left to do."})
    return jsonify(rec)


@app.get("/api/session")
def session_active():
    s = db.active_session()
    if s:
        s["task"] = _enrich_task(db.get_task(s["task_id"])) if db.get_task(s["task_id"]) else None
    return jsonify({"session": s})


@app.post("/api/sessions")
def session_start():
    body = request.get_json(silent=True) or {}
    task_id = body.get("task_id")
    minutes = int(body.get("minutes") or HARDWARE["minutes"])
    if not task_id or not db.get_task(task_id):
        return jsonify({"error": "Valid task_id required."}), 400
    return jsonify({"session": db.start_session(task_id, minutes)})


@app.post("/api/sessions/<int:session_id>/extend")
def session_extend(session_id):
    body = request.get_json(silent=True) or {}
    s = db.extend_session(session_id, int(body.get("minutes") or 15))
    if s is None:
        return jsonify({"error": "Session not found or already ended."}), 404
    return jsonify({"session": s})


@app.post("/api/sessions/<int:session_id>/finish")
def session_finish(session_id):
    s = db.finish_session(session_id)
    if s is None:
        return jsonify({"error": "Session not found."}), 404
    return jsonify({"session": s})


# ---------------- ESP32 hardware ----------------

@app.get("/api/hardware/state")
def hw_state():
    s = db.active_session()
    rec = _recommend(HARDWARE["minutes"])
    return jsonify(
        {
            "minutes": HARDWARE["minutes"],
            "session": s,
            "recommended": rec["task"]["title"] if rec else None,
            "progress": db.progress_counts(),
        }
    )


@app.post("/api/hardware")
def hw_action():
    """Knob/buttons contract for the ESP32:
    {action: set_minutes|start|done|skip|more_time, value?: number}
    """
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    value = int(body.get("value") or 0)

    if action == "set_minutes":
        HARDWARE["minutes"] = max(5, min(480, value))
        return jsonify({"minutes": HARDWARE["minutes"]})

    if action == "more_time":
        s = db.active_session()
        if not s:
            return jsonify({"error": "No active session."}), 400
        return jsonify({"session": db.extend_session(s["id"], value or 15)})

    if action == "done":
        s = db.active_session()
        if s:
            db.finish_session(s["id"])
            db.update_task(s["task_id"], {"status": "done"})
        HARDWARE["skip_ids"].clear()
        return jsonify({"session": s, "recommended": (_recommend(HARDWARE["minutes"]) or {}).get("task")})

    if action == "skip":
        s = db.active_session()
        if s:
            HARDWARE["skip_ids"].add(s["task_id"])
            db.finish_session(s["id"])
        rec = _recommend(HARDWARE["minutes"], exclude_ids=HARDWARE["skip_ids"])
        return jsonify({"recommended": rec["task"] if rec else None, "reason": rec["reason"] if rec else None})

    if action == "start":
        task_id = body.get("task_id")
        if not task_id:
            rec = _recommend(HARDWARE["minutes"], exclude_ids=HARDWARE["skip_ids"])
            if not rec:
                return jsonify({"error": "Nothing to recommend."}), 404
            task_id = rec["task"]["id"]
        return jsonify({"session": db.start_session(task_id, HARDWARE["minutes"])})

    return jsonify({"error": f"Unknown action: {action}"}), 400


# ---------------- Waterloo Learn sync ----------------

@app.get("/api/learn/status")
def learn_status():
    return jsonify(learn.login_status())


@app.post("/api/learn/login")
def learn_login():
    learn.start_login()
    return jsonify(learn.login_status())


@app.post("/api/learn/sync")
def learn_sync():
    try:
        return jsonify(learn.sync_all())
    except learn.AuthExpired as e:
        return jsonify({"error": str(e), "auth": False}), 401
    except Exception as e:
        return jsonify({"error": f"Learn sync failed: {e}"}), 502


# ---------------- research (Steel agent) ----------------

@app.post("/api/research")
def research():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY is not set. Add it in the API keys panel."}), 503

    body = request.get_json(silent=True) or {}
    assignment = body.get("assignment")
    if not assignment or not assignment.get("title"):
        return jsonify({"error": "Missing assignment payload."}), 400

    try:
        return jsonify(
            start_research_job(
                assignment,
                require_login=bool(body.get("require_login")),
                window=body.get("window"),
            )
        )
    except Exception as e:
        return jsonify({"error": f"Research failed to start: {e}"}), 502


@app.get("/api/research/<job_id>")
def research_status(job_id):
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "Unknown research job."}), 404
    return jsonify(job)


@app.post("/api/research/<job_id>/resume")
def research_resume(job_id):
    if not resume_job(job_id):
        return jsonify({"error": "Unknown research job."}), 404
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", "5000")))
