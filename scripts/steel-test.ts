import Steel from "steel-sdk";
import { chromium } from "playwright";
import dotenv from "dotenv";

dotenv.config({ path: ".env.local" });

async function main() {
  const apiKey = process.env.STEEL_API_KEY;

  if (!apiKey) {
    throw new Error("Missing STEEL_API_KEY in .env.local");
  }

  const steel = new Steel({ steelAPIKey: apiKey });
  const session = await steel.sessions.create();

  try {
    const connectionUrl = new URL(session.websocketUrl);
    connectionUrl.searchParams.set("apiKey", apiKey);

    const browser = await chromium.connectOverCDP(
      connectionUrl.toString()
    );

    const page = browser.contexts()[0].pages()[0];

    await page.goto("https://example.com");
    console.log("Page title:", await page.title());
  } finally {
    await steel.sessions.release(session.id);
  }
}

main().catch(() => {
  console.error("Steel test failed. Check your key and Steel dashboard.");
  process.exitCode = 1;
});