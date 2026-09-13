"""Waterloo Learn (D2L Brightspace) integration via the Valence API.

Adapted from the user's cookie-extraction script: sign in once in a headed local
browser (WatIAM SSO + Duo), persist the storage state to waterloo_auth.json, then
pull enrollments / dropbox folders / quizzes through /d2l/api/* in a single
headless context — no Steel session required.
"""

import json
import os
import threading
from datetime import datetime, timezone

AUTH_FILE = os.path.join(os.path.dirname(__file__), "waterloo_auth.json")
BASE_URL = "https://learn.uwaterloo.ca"
LP_VERSION = "1.43"
LE_VERSION = "1.43"

LOGIN_STATE = {"running": False, "error": None}


class AuthExpired(Exception):
    pass


def is_authenticated() -> bool:
    return os.path.exists(AUTH_FILE)


def start_login() -> None:
    """Launch a headed browser for WatIAM/Duo sign-in; saves storage state."""
    if LOGIN_STATE["running"]:
        return

    def _run():
        LOGIN_STATE.update(running=True, error=None)
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                context = browser.new_context()
                page = context.new_page()
                page.goto(BASE_URL)
                # User completes WatIAM + Duo; dashboard means success.
                page.wait_for_url("**/d2l/home**", timeout=300_000)
                context.storage_state(path=AUTH_FILE)
                browser.close()
        except Exception as e:
            LOGIN_STATE["error"] = str(e)
        finally:
            LOGIN_STATE["running"] = False

    threading.Thread(target=_run, daemon=True).start()


def login_status() -> dict:
    return {"authenticated": is_authenticated(), **LOGIN_STATE}


def _due_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except (ValueError, AttributeError):
        return None


def sync_all() -> dict:
    """Pull enrollments + dropbox folders + quizzes in one headless context."""
    if not is_authenticated():
        raise AuthExpired("Not signed in — run the Learn login first.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=AUTH_FILE)
        page = context.new_page()
        page.goto(f"{BASE_URL}/d2l/home", wait_until="domcontentloaded")

        def api(path):
            return page.evaluate(
                """async (path) => {
                    try {
                        const res = await fetch(path);
                        if (!res.ok) return { error: res.status, statusText: res.statusText };
                        return await res.json();
                    } catch (err) { return { error: err.toString() }; }
                }""",
                path,
            )

        data = api(f"/d2l/api/lp/{LP_VERSION}/enrollments/myenrollments/")
        if isinstance(data, dict) and data.get("error") in (401, 403):
            browser.close()
            raise AuthExpired("Saved login expired — sign in again.")
        enrollments = data.get("Items", []) if isinstance(data, dict) else []
        courses = [
            {"id": it["OrgUnit"]["Id"], "name": it["OrgUnit"]["Name"]}
            for it in enrollments
            if isinstance(it, dict) and it.get("OrgUnit", {}).get("Type", {}).get("Code") == "Course Offering"
        ]

        items = []
        for c in courses:
            cid = c["id"]
            folders = api(f"/d2l/api/le/{LE_VERSION}/{cid}/dropbox/folders/")
            if isinstance(folders, list):
                for f in folders:
                    items.append(
                        {
                            "course": c["name"],
                            "title": f.get("Name"),
                            "type": "homework",
                            "due_date": _due_date(f.get("DueDate")),
                            "due_text": f.get("DueDate") or "",
                            "description": (f.get("CustomInstructions") or {}).get("Text") or "",
                            "grade_weight": f"{f.get('TotalPoints')} pts" if f.get("TotalPoints") else "",
                            "link": f"{BASE_URL}/d2l/lms/dropbox/user/folder_submit_files.d2l?db={f.get('Id')}&ou={cid}",
                        }
                    )
            quizzes = api(f"/d2l/api/le/{LE_VERSION}/{cid}/quizzes/")
            objects = quizzes.get("Objects", []) if isinstance(quizzes, dict) else []
            for q in objects:
                items.append(
                    {
                        "course": c["name"],
                        "title": q.get("Name"),
                        "type": "quiz",
                        "due_date": _due_date(q.get("EndDate")),
                        "due_text": f"closes {q.get('EndDate')}" if q.get("EndDate") else "",
                        "description": "",
                        "grade_weight": "",
                        "link": f"{BASE_URL}/d2l/lms/quizzing/user/quiz_summary.d2l?qi={q.get('QuizId')}&ou={cid}",
                    }
                )

        browser.close()
        return {"courses": courses, "items": items}
