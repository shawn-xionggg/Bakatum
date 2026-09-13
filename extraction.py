"""Use Claude to extract structured assignments from raw syllabus text."""

import os
from datetime import date

import anthropic

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

EXTRACT_TOOL = {
    "name": "report_assignments",
    "description": "Report every gradable assignment, exam, quiz, project, paper, lab, and required reading found in the syllabus.",
    "input_schema": {
        "type": "object",
        "properties": {
            "course_name": {"type": "string", "description": "Course code and title, e.g. 'CS 101 — Intro to CS'"},
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["exam", "quiz", "project", "paper", "homework", "lab", "reading", "participation", "other"],
                        },
                        "due_date": {
                            "type": "string",
                            "description": "Resolved ISO 8601 date (YYYY-MM-DD), or empty string if it can't be determined.",
                        },
                        "due_text": {
                            "type": "string",
                            "description": "The raw due-date wording from the syllabus, e.g. 'Week 5 Friday'.",
                        },
                        "description": {"type": "string", "description": "What the assignment involves, 1-2 sentences."},
                        "grade_weight": {"type": "string", "description": "e.g. '15% of final grade', if stated."},
                        "importance": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "high = major grade stake (exams, big projects, papers); medium = meaningful weight (labs, quizzes); low = routine (homework, readings).",
                        },
                        "estimated_hours": {"type": "number", "description": "Rough hours needed to complete."},
                        "plan_bullets": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "2-4 concrete bullets on how to tackle this assignment (first steps, key deliverable).",
                        },
                        "topics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key topics/skills, used later to find study resources.",
                        },
                    },
                    "required": ["title", "type", "importance"],
                },
            },
        },
        "required": ["assignments"],
    },
}


def extract_assignments(syllabus_text: str, window_start: date, window_end: date) -> dict:
    client = anthropic.Anthropic()
    today = date.today()

    prompt = f"""You are analyzing a university course syllabus. Today is {today.isoformat()}.
The student cares about the window {window_start.isoformat()} through {window_end.isoformat()},
but extract EVERY gradable item in the syllabus, not just ones in that window.

Rules:
- Resolve relative dates ("Week 3", "Sept 15", "third Friday") to a YYYY-MM-DD due_date
  using the semester/term dates implied by the syllabus. Use the current academic year
  ({today.year}) unless the syllabus clearly states otherwise. If a date truly cannot be
  resolved, leave due_date empty and keep the raw wording in due_text.
- Include exams, quizzes, projects, papers, homework, labs, and required readings.
  Skip recurring boilerplate (e.g. "attend office hours") unless it is graded.
- Rate importance by grade stake and effort: exams and major projects/papers are high,
  labs/quizzes medium, routine homework/readings low.

SYLLABUS TEXT:
<syllabus>
{syllabus_text}
</syllabus>

Call report_assignments with everything you find."""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "report_assignments"},
        messages=[{"role": "user", "content": prompt}],
    )

    for block in resp.content:
        if block.type == "tool_use" and block.name == "report_assignments":
            return {
                "course_name": block.input.get("course_name", ""),
                "assignments": block.input.get("assignments", []),
            }
    raise RuntimeError("Claude didn't return structured assignments — try again.")
