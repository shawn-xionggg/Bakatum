import os
import json
import asyncio
from anthropic import AsyncAnthropic
from steel import Steel
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv("envs.local")

# ---------------------------------------------------------------------------
# 1. Define the tools Claude is allowed to call. Each one maps 1:1 to a
#    Playwright action we know how to execute against the Steel session.
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "navigate",
        "description": "Navigate the browser to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Fully qualified URL to load."}
            },
            "required": ["url"],
        },
    },
    {
        "name": "click",
        "description": "Click the first element matching a CSS selector.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to click."}
            },
            "required": ["selector"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into the first element matching a CSS selector.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "extract_text",
        "description": "Return the inner text of the first element matching a CSS selector.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to read text from."}
            },
            "required": ["selector"],
        },
    },
    {
        "name": "finish",
        "description": "Call this when the task is complete. Provide a final summary of what was found/done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"}
            },
            "required": ["summary"],
        },
    },
]

SYSTEM_PROMPT = (
    "You are a browser automation agent. You control a real Chromium browser "
    "through tools: navigate, click, type_text, extract_text, and finish. "
    "Call exactly one tool per turn. Look at the result of each tool call before "
    "deciding the next action. When the task is complete, call `finish` with a "
    "summary of what you accomplished or found. Do not narrate outside of tool calls."
)


async def execute_tool(page, tool_name: str, tool_input: dict) -> str:
    """Run the requested Playwright action and return a text result for Claude."""
    try:
        if tool_name == "navigate":
            await page.goto(tool_input["url"], wait_until="domcontentloaded")
            return f"Navigated to {tool_input['url']}. Page title: {await page.title()}"

        elif tool_name == "click":
            await page.locator(tool_input["selector"]).first.click()
            return f"Clicked selector '{tool_input['selector']}'."

        elif tool_name == "type_text":
            await page.locator(tool_input["selector"]).first.fill(tool_input["text"])
            return f"Typed text into selector '{tool_input['selector']}'."

        elif tool_name == "extract_text":
            text = await page.locator(tool_input["selector"]).first.inner_text()
            return f"Extracted text: {text}"

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        return f"Error executing {tool_name}: {e}"


async def claude_steel_agent(task: str, max_turns: int = 10):
    anthropic = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    steel = Steel(steel_api_key=os.getenv("STEEL_API_KEY"))

    session = None
    try:
        print("Provisioning Steel browser...")
        session = steel.sessions.create(use_proxy=False, solve_captcha=False)
        print(f"Watch Claude work live: {session.session_viewer_url}")

        async with async_playwright() as p:
            ws_endpoint = f"{session.websocket_url}&apiKey={os.getenv('STEEL_API_KEY')}"
            browser = await p.chromium.connect_over_cdp(ws_endpoint)
            page = browser.contexts[0].pages[0]

            # Conversation state Claude sees. Starts with the task description.
            messages = [{"role": "user", "content": task}]

            for turn in range(max_turns):
                print(f"\n--- Turn {turn + 1}: consulting Claude ---")
                response = await anthropic.messages.create(
                    model="claude-sonnet-5",
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    tool_choice={"type": "any"},  # force a tool call every turn
                    messages=messages,
                )

                # Record Claude's turn (may include text + a tool_use block)
                messages.append({"role": "assistant", "content": response.content})

                tool_use_block = next(
                    (b for b in response.content if b.type == "tool_use"), None
                )
                if tool_use_block is None:
                    print("Claude did not call a tool; stopping.")
                    break

                name, tool_input, tool_id = (
                    tool_use_block.name,
                    tool_use_block.input,
                    tool_use_block.id,
                )
                print(f"Claude called: {name}({tool_input})")

                if name == "finish":
                    print(f"\nAgent finished: {tool_input.get('summary')}")
                    break

                result_text = await execute_tool(page, name, tool_input)
                print(f"Result: {result_text}")

                # Feed the tool result back to Claude for the next turn
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": result_text,
                            }
                        ],
                    }
                )

            await browser.close()

    except Exception as e:
        print(f"Agent failed: {e}")

    finally:
        if session:
            print("Releasing Steel session...")
            steel.sessions.release(session.id)


if __name__ == "__main__":
    task = "find information about ece150 in waterloo university, assignment 4 and the due date and notes for it"
    asyncio.run(claude_steel_agent(task))