"""Research agent: Claude drives a tool-use loop against a live Steel browser session.

When STEEL_API_KEY is set, a real cloud browser session is created and driven over
CDP via Playwright — the session's viewer URL is handed to the frontend so the user
can watch the research happen live. Without a key, the same tools fall back to
plain HTTP fetches and no live view is available.

Jobs run in background threads; GET /api/research/<job_id> polls for the result.
"""

import base64
import json
import os
import threading
import time
import uuid
from urllib.parse import quote_plus

import anthropic
import httpx
from bs4 import BeautifulSoup

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_ROUNDS = 12
MAX_SEARCHES = 3
PAGE_CHAR_LIMIT = 8_000
SEARCH_CHAR_LIMIT = 6_000
LEARN_URL = "https://learn.uwaterloo.ca"
LOGIN_TIMEOUT_S = 300

JOBS: dict[str, dict] = {}

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web and return result titles, links, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A focused search query."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "browse_url",
        "description": "Open a URL in the browser and return the page state (text, links, screenshot).",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "purpose": {"type": "string", "description": "What you're looking for on this page."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "click_link",
        "description": "Click a link, tile, or button on the current page by its visible text, then return the new page state (text, links, screenshot). Use this for JS-driven elements whose href is missing or '#', or when a tile needs a real click.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Visible text of the element to click."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "submit_resources",
        "description": "Submit the final plan and curated resources. Call this when research is done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "2-3 sentence overview of the approach."},
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Bullet-point plan to complete the assignment within the timeframe.",
                },
                "resources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "enum": ["tutorial", "documentation", "video", "paper", "tool", "example", "practice", "other"],
                            },
                            "why": {"type": "string", "description": "One sentence on why this helps with THIS assignment."},
                        },
                        "required": ["title", "url", "kind", "why"],
                    },
                },
                "assignments_found": {
                    "type": "array",
                    "description": "Every gradable item found on Waterloo Learn pages, with its link.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                            "due": {"type": "string", "description": "Due date/time as shown on Learn."},
                            "course": {"type": "string"},
                            "in_window": {"type": "boolean", "description": "True if due within the student's timeframe."},
                        },
                        "required": ["title", "url"],
                    },
                },
            },
            "required": ["summary", "steps", "resources"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool backends
# ---------------------------------------------------------------------------

class _HttpTools:
    """Fallback: DuckDuckGo + readability-ish extraction over plain HTTP."""

    name = "http"

    def web_search(self, query: str) -> str:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            html = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True).text
            soup = BeautifulSoup(html, "html.parser")
            lines = []
            for a in soup.select("a.result__a")[:10]:
                res = a.find_parent("div", class_="result")
                snip = res.select_one(".result__snippet") if res else None
                lines.append(f"- {a.get_text(strip=True)}\n  {a.get('href','')}\n  {snip.get_text(strip=True) if snip else ''}")
            return "\n".join(lines)[:SEARCH_CHAR_LIMIT] or "No results found."
        except Exception as e:
            return f"Search failed: {e}"

    def browse_url(self, url: str, purpose: str = "") -> str:
        try:
            html = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True).text
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            return soup.get_text(" ", strip=True)[:PAGE_CHAR_LIMIT]
        except Exception as e:
            return f"Browse failed: {e}"

    def click_link(self, text: str) -> str:
        return "Click unavailable — no live browser session."

    def close(self):
        pass


class _SteelSessionTools:
    """Drives a live Steel cloud-browser session via Playwright over CDP."""

    name = "steel-session"

    def __init__(self, session):
        from playwright.sync_api import sync_playwright

        self._session = session
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(session.websocket_url, timeout=20_000)
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()

    def _screenshot_block(self) -> dict | None:
        """Current viewport as a base64 JPEG image block for Claude vision."""
        try:
            shot = self._page.screenshot(type="jpeg", quality=70)
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(shot).decode(),
                },
            }
        except Exception:
            return None

    def web_search(self, query: str) -> list:
        try:
            self._page.goto(
                f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            results = self._page.evaluate(
                """[...document.querySelectorAll('.result')].slice(0, 10).map(r => ({
                    title: r.querySelector('.result__a')?.innerText || '',
                    url: r.querySelector('.result__a')?.href || '',
                    snippet: r.querySelector('.result__snippet')?.innerText || ''
                }))"""
            )
            lines = [f"- {r['title']}\n  {r['url']}\n  {r['snippet']}" for r in results]
            blocks = [{"type": "text", "text": "\n".join(lines)[:SEARCH_CHAR_LIMIT] or "No results found."}]
            shot = self._screenshot_block()
            if shot:
                blocks.append(shot)
            return blocks
        except Exception as e:
            return [{"type": "text", "text": f"Search failed: {e}"}]

    def _page_state(self) -> list:
        """Current page as Claude-visible blocks: text, link list, screenshot."""
        # Nudge lazy-rendered content (Learn tiles/lists) into existence.
        try:
            self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._page.wait_for_timeout(800)
            self._page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        text = self._page.evaluate("document.body ? document.body.innerText : ''")
        title = self._page.title()
        links = self._page.evaluate(
            """[...document.querySelectorAll('a[href]')]
               .map(a => ({
                   text: (a.innerText || a.getAttribute('aria-label') || a.title || '').trim().slice(0, 90),
                   href: a.href
               }))
               .filter(l => l.href && l.href.startsWith('http'))"""
        )
        seen, link_lines = set(), []
        for l in links:
            if l["href"] in seen:
                continue
            seen.add(l["href"])
            link_lines.append(f"- {l['text'] or '(no label)'} → {l['href']}")
            if len(link_lines) >= 60:
                break
        body = (
            f"# {title}\nURL: {self._page.url}\n\n{text[:PAGE_CHAR_LIMIT]}\n\n"
            f"LINKS ON THIS PAGE (follow with browse_url, or click_link by text):\n" + "\n".join(link_lines)
        )
        blocks = [{"type": "text", "text": body[: PAGE_CHAR_LIMIT + 4_000]}]
        shot = self._screenshot_block()
        if shot:
            blocks.append(shot)
        return blocks

    def browse_url(self, url: str, purpose: str = "") -> list:
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            self._page.wait_for_timeout(1_500)
            return self._page_state()
        except Exception as e:
            return [{"type": "text", "text": f"Browse failed: {e}"}]

    def click_link(self, text: str) -> list:
        """Click by visible text (real mouse click — works on JS tiles), return new state."""
        try:
            loc = self._page.get_by_role("link", name=text).first
            if loc.count() == 0:
                loc = self._page.get_by_text(text, exact=False).first
            loc.click(timeout=8_000)
            self._page.wait_for_timeout(2_500)  # let navigation/rendering settle
            return self._page_state()
        except Exception as e:
            return [{"type": "text", "text": f"Click on '{text}' failed: {e}"}]

    def close(self):
        try:
            self._browser.close()
        finally:
            self._pw.stop()


def _dispatch(tools, name: str, inputs: dict) -> str:
    if name == "web_search":
        return tools.web_search(inputs.get("query", ""))
    if name == "browse_url":
        return tools.browse_url(inputs.get("url", ""), inputs.get("purpose", ""))
    if name == "click_link":
        return tools.click_link(inputs.get("text", ""))
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------

def start_research_job(assignment: dict, require_login: bool = False, window: dict | None = None) -> dict:
    """Create a Steel session (if keyed) and kick off the agent loop in a thread.

    When require_login is set, the job opens Waterloo Learn in the session and
    pauses at 'awaiting_login' until the user confirms they signed in.
    """
    job_id = uuid.uuid4().hex[:12]
    job = {
        "status": "running",
        "viewer_url": None,
        "result": None,
        "error": None,
        "live_view": False,
        "log": [],
        "resume_event": threading.Event(),
        "require_login": False,
    }
    JOBS[job_id] = job

    # Watchdog: never leave a job running forever.
    def _watchdog():
        if job["status"] in ("running", "awaiting_login"):
            job["status"] = "error"
            job["error"] = "Research timed out — the agent didn't finish in 4 minutes."

    threading.Timer(240, _watchdog).start()

    session = None
    if os.environ.get("STEEL_API_KEY"):
        try:
            from steel import Steel

            steel = Steel()
            session = steel.sessions.create(api_timeout=20_000)
            # debugUrl is the interactive, embeddable viewer — unauthenticated by
            # design. interactive=true lets the user click/type (e.g. SSO login).
            debug_url = getattr(session, "debug_url", None)
            job["viewer_url"] = (
                f"{debug_url}?interactive=true&showControls=true" if debug_url
                else f"https://api.steel.dev/v1/sessions/{session.id}/player"
            )
            job["require_login"] = require_login
            # Wait until the browser is actually live so the embedded player
            # connects on first load instead of showing a refresh prompt.
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    if getattr(steel.sessions.retrieve(session.id), "status", None) == "live":
                        break
                except Exception:
                    pass
                time.sleep(1)
        except Exception as e:
            job["log"].append(f"Steel session unavailable ({e}) — falling back to web fetches")
            session = None

    thread = threading.Thread(target=_run_job, args=(job, assignment, session, window), daemon=True)
    thread.start()
    return {"job_id": job_id, "viewer_url": job["viewer_url"], "require_login": job["require_login"]}


def get_job(job_id: str) -> dict | None:
    job = JOBS.get(job_id)
    if not job:
        return None
    out = {"status": job["status"], "viewer_url": job["viewer_url"], "log": job["log"][-4:]}
    if job["status"] == "done":
        out.update(job["result"])
    if job["status"] == "error":
        out["error"] = job["error"]
    return out


def resume_job(job_id: str) -> bool:
    job = JOBS.get(job_id)
    if not job:
        return False
    job["resume_event"].set()
    return True


def _run_job(job: dict, assignment: dict, session, window: dict | None) -> None:
    tools = None
    try:
        job["log"].append("job started" + (" (steel session)" if session else " (no steel)"))
        if session is not None:
            try:
                tools = _SteelSessionTools(session)
            except Exception:
                tools = _HttpTools()  # session stays alive; still viewable
        else:
            tools = _HttpTools()
        job["live_view"] = tools.name == "steel-session"

        # Human-in-the-loop: park on the Learn login page until the user
        # confirms they signed in (or the timeout elapses and we proceed).
        if job["require_login"] and tools.name == "steel-session":
            try:
                tools.browse_url(LEARN_URL)
            except Exception:
                pass
            job["status"] = "awaiting_login"
            job["resume_event"].wait(timeout=LOGIN_TIMEOUT_S)

        job["status"] = "running"
        job["result"] = _agent_loop(tools, assignment, learn_mode=job["require_login"], window=window, job=job)
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        print(f"[research] job failed: {e}", flush=True)
    finally:
        if tools:
            tools.close()
        if session is not None:
            try:
                from steel import Steel

                Steel().sessions.release(session.id)
            except Exception:
                pass


def _extract_submit(block) -> dict:
    return {
        "summary": block.input.get("summary", ""),
        "steps": block.input.get("steps", []),
        "resources": block.input.get("resources", []),
        "assignments_found": block.input.get("assignments_found", []),
    }


def _agent_loop(tools, assignment: dict, learn_mode: bool = False, window: dict | None = None, job: dict | None = None) -> dict:
    client = anthropic.Anthropic()
    backend = "a live browser" if tools.name == "steel-session" else "web fetches"
    window_text = ""
    if window and window.get("start") and window.get("end"):
        window_text = f"The student's timeframe is {window['start']} through {window['end']}."

    prompt = f"""You are helping a university student who is stuck on an assignment. Research
the open web using {backend} and return (a) a bullet-point plan to finish it in time and
(b) a curated list of the BEST free help resources — video tutorials, walkthroughs,
worked examples, docs, practice problems — specific to THIS assignment's topics.
{window_text}

Think like a student searching for help: for each topic, prefer well-known high-quality
free resources (e.g. The Organic Chemistry Tutor, Khan Academy, 3Blue1Brown, MIT OCW,
Paul's Online Notes, GeeksforGeeks, official docs) over random blogs. Match resources
to the assignment's topics — not generic study tips.

Assignment:
{json.dumps(assignment, indent=2)}

Budget: at most {MAX_SEARCHES} web_search calls. Then browse the most promising results
to verify they're real and relevant, and call submit_resources with a steps plan and
4-8 resources. Do not fabricate URLs — only submit URLs you actually saw in search
results or browsed pages."""

    messages = [{"role": "user", "content": prompt}]
    searches_used = 0

    for _ in range(MAX_ROUNDS):
        tools_for_call = TOOLS if searches_used < MAX_SEARCHES else [t for t in TOOLS if t["name"] != "web_search"]
        resp = client.messages.create(model=MODEL, max_tokens=4096, tools=tools_for_call, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})

        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            if block.name == "submit_resources":
                return _extract_submit(block)
            if block.name == "web_search":
                searches_used += 1
                if searches_used > MAX_SEARCHES:
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id,
                         "content": "Search budget reached — browse URLs you already have or submit."}
                    )
                    continue
            if job is not None:
                desc = block.input.get("query") or block.input.get("url") or block.input.get("text") or ""
                line = f"{block.name}: {desc[:80]}"
                job["log"].append(line)
                print(f"[research {id(job) % 9999}] {line}", flush=True)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": _dispatch(tools, block.name, block.input)}
            )

        if not tool_results:
            break
        messages.append({"role": "user", "content": tool_results})

    messages.append({"role": "user", "content": "Submit your best findings now with submit_resources."})
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        tools=TOOLS,
        tool_choice={"type": "tool", "name": "submit_resources"},
        messages=messages,
    )
    for block in resp.content:
        if block.type == "tool_use":
            return _extract_submit(block)
    raise RuntimeError("Research agent didn't return results — try again.")
