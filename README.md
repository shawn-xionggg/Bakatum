# syla

Upload a university syllabus (PDF, HTML, or TXT) and get back a prioritized
assignment list — each item labeled by **urgency** (computed from the due date)
and **importance** (grade stake) — then research relevant resources for any
assignment with one click.

## How it works

1. **Upload** — `POST /api/upload` extracts text from the syllabus and asks
   Claude (Anthropic API, structured tool-use output) to return every gradable
   item with resolved due dates, type, grade weight, estimated effort, and topics.
2. **Scoring** — `scoring.py` deterministically labels urgency
   (overdue / critical ≤2d / high ≤7d / medium ≤14d / low / unscheduled) and a
   combined priority score, so labels are consistent and reproducible.
3. **Picks** — the backend flags up to 3 `recommended` assignments that are
   inside your window and actually doable (participation/credit-only items are
   excluded). Each card shows a bullet-point plan from the extraction pass.
4. **Research** — `POST /api/research` starts a background job. With
   `STEEL_API_KEY` it creates a live [Steel](https://steel.dev) cloud-browser
   session driven by Playwright over CDP; the response `viewer_url` is embedded
   in the page so you can watch the agent browse in real time. Claude's tool
   loop (`web_search`, `browse_url`) returns a step plan plus curated links via
   `GET /api/research/<job_id>` polling. Without a Steel key it falls back to
   plain HTTP with no live view.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
copy .env.example .env          # add ANTHROPIC_API_KEY (and optionally STEEL_API_KEY)
python app.py
```

Open http://localhost:5000.

## Devin / MCP integration

`.devin/mcp_config.json` wires the [Steel MCP server](https://github.com/steel-dev/steel-mcp-server)
into this project's Devin environment (`npx -y github:steel-dev/steel-mcp-server`).
Put your key in `.devin/mcp_config.local.json` (gitignored) so the agent can
browse the web while developing.

## Layout

| File | Role |
|---|---|
| `app.py` | Flask routes: `/`, `/api/upload`, `/api/research`, `/api/health` |
| `parsing.py` | PDF (pypdf) / HTML (BeautifulSoup) / TXT → text |
| `extraction.py` | Claude structured extraction of assignments |
| `scoring.py` | Urgency, importance, priority scoring |
| `research.py` | Claude + Steel tool-use research loop |
| `templates/`, `static/` | Single-page frontend (vanilla JS) |
