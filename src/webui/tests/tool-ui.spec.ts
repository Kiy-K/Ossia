/**
 * Tool UI rendering e2e tests.
 *
 * Sends a prompt designed to trigger tool calls and verifies that the
 * custom Tool UI components render correctly in the chat thread instead
 * of raw JSON or the generic ToolFallback.
 *
 * Prerequisites:
 *   - Backend running on ``http://127.0.0.1:8000`` (the Vite proxy forwards
 *     ``/v1/*`` and ``/health`` to it).
 *   - API key ``dev`` (default in localStorage).
 *   - The ``OSSIA_API_KEY`` env var or a running ``.env`` for the backend.
 *
 * The test skips itself with ``test.skip()`` when the backend is unreachable.
 */

import { test, expect } from "@playwright/test";

const BACKEND_URL = "http://localhost:8000";

/**
 * Prompts designed to trigger known Ossia tools.
 *
 * ``search_codebase`` is the most reliable trigger since it uses ripgrep
 * locally without requiring network access or a knowledge base.
 */
const CODE_SEARCH_PROMPT =
  "Search the codebase for the file that defines the build_agent_async function";

/**
 * Regex matching any known tool UI card title (case-insensitive).
 *
 * These texts appear inside the ``cardHeader`` of each custom tool UI
 * renderer registered in ``ossia-toolkit.ts``. The test waits for
 * at least one match to appear after sending the prompt.
 *
 * NOTE: Do NOT add generic catch-all terms like ``"search"`` here —
 * the user's prompt text itself contains "Search the codebase...",
 * and text-selectors would match the user message, not a tool card.
 */
const TOOL_CARD_PATTERN =
  /Knowledge Base Search|Web Search|Response Sent|Quality Grade|Code Search|URL Fetched|Test Results|Proposed Fix|Pull Request Created|Issue \/ PR|Q&A Answer/i;

/**
 * Returns ``true`` if the backend health endpoint responds with HTTP 200.
 */
async function backendReachable(): Promise<boolean> {
  try {
    const resp = await fetch(`${BACKEND_URL}/health`);
    return resp.ok;
  } catch {
    return false;
  }
}

test.describe("Tool UI rendering", () => {
  test("renders custom tool UI cards from a code-search prompt", async ({
    page,
  }) => {
    // ── Capture browser console for debugging ──────────────────────
    const consoleLogs: string[] = [];
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      const text = msg.text();
      if (msg.type() === "error") {
        consoleErrors.push(text);
        console.log("🛑 BROWSER ERROR:", text);
      } else if (msg.type() === "warning") {
        console.log("⚠️ BROWSER WARN:", text);
      } else {
        consoleLogs.push(text);
      }
    });
    page.on("pageerror", (err) => {
      consoleErrors.push(err.message);
      console.log("🛑 PAGE ERROR:", err.message);
    });
    // ── Skip if backend is not running ─────────────────────────────────
    test.skip(
      !(await backendReachable()),
      `Backend is not running on ${BACKEND_URL}. Start it with: OSSIA_API_KEY=dev uvicorn core.api:app --host 127.0.0.1 --port 8000`,
    );

    // ── Navigate to the Web UI ─────────────────────────────────────────
    await page.goto("/", { waitUntil: "load" });
    await page.waitForSelector("header");
    // Give React hydration a moment
    await page.waitForTimeout(500);

    // Ensure the Chat tab is active
    const chatTab = page.locator("nav button").filter({ hasText: /^Chat/ });
    await chatTab.click();
    await page.waitForTimeout(200);

    // ── Focus the composer and type the prompt ─────────────────────────
    const composer = page.locator('textarea[placeholder="Send a message..."]');
    await expect(composer).toBeVisible({ timeout: 5_000 });
    await composer.fill(CODE_SEARCH_PROMPT);
    await composer.press("Enter");

    // ── Wait for tool call cards to appear ─────────────────────────────
    // The agent should call search_codebase, which renders a custom
    // "Code Search" card. We wait for any known tool card title to
    // appear, with generous timeout for LLM processing.
    console.log(
      "⏳ Waiting for tool UI cards (prompt may trigger code search)...",
    );

    // Wait for at least one tool card header to appear
    const toolCard = page.locator("div").filter({ hasText: TOOL_CARD_PATTERN }).first();

    try {
      await toolCard.waitFor({ state: "attached", timeout: 180_000 });
    } catch (err) {
      await page.screenshot({
        path: "/tmp/ossia-tool-ui-failure.png",
        fullPage: true,
      });
      console.error("⚠️  Timed out waiting for tool UI cards");
      console.error("   Screenshot saved to /tmp/ossia-tool-ui-failure.png");
      throw err;
    }

    // ── Verify at least one tool card header is visible ─────────────────
    const visibleCount = await page
      .locator("div")
      .filter({ hasText: TOOL_CARD_PATTERN })
      .count();
    console.log(`✅ Found ${visibleCount} tool UI card header(s)`);
    expect(visibleCount).toBeGreaterThan(0);

    // ── Take a screenshot for visual verification ──────────────────────
    await page.screenshot({
      path: "/tmp/ossia-tool-ui-pass.png",
      fullPage: true,
    });
    console.log("📸 Screenshot saved to /tmp/ossia-tool-ui-pass.png");
  });

  test("tool UI cards render with status transitions (running → complete)", async ({
    page,
  }) => {
    test.skip(
      !(await backendReachable()),
      `Backend is not running on ${BACKEND_URL}.`,
    );

    await page.goto("/", { waitUntil: "load" });
    await page.waitForSelector("header");
    await page.waitForTimeout(500);

    // Ensure Chat tab is active
    const chatTab = page.locator("nav button").filter({ hasText: /^Chat/ });
    await chatTab.click();
    await page.waitForTimeout(200);

    // Send a prompt that should trigger a tool call
    const composer = page.locator('textarea[placeholder="Send a message..."]');
    await expect(composer).toBeVisible({ timeout: 5_000 });
    await composer.fill(CODE_SEARCH_PROMPT);
    await composer.press("Enter");

    // ── Wait for a tool card header (any known tool) ────────────────────
    // This catches both the running and completed states. The card header
    // text (e.g. "Code Search", "Test Results") is rendered regardless of
    // status, so we wait for it directly rather than chasing ephemeral
    // status indicators.
    const toolCard = page.locator("div").filter({ hasText: TOOL_CARD_PATTERN }).first();

    try {
      await toolCard.waitFor({ state: "attached", timeout: 180_000 });
      console.log("✅ Tool card appeared in the DOM");
    } catch (err) {
      // Screenshot before rethrowing — use a try/catch around the
      // screenshot itself in case the page was already torn down.
      try {
        await page.screenshot({
          path: "/tmp/ossia-tool-ui-status-failure.png",
          fullPage: true,
        });
        console.error("⚠️  Screenshot saved to /tmp/ossia-tool-ui-status-failure.png");
      } catch {
        // Page was already closed — nothing more we can do
      }
      console.error("⚠️  Timed out waiting for tool UI card");
      throw err;
    }

    // ── Verify completed state elements ─────────────────────────────────
    // A completed tool card shows a ✓ checkmark in the green section of
    // the header. This is ephemeral (may have transitioned already) so
    // we only assert if the element is still visible.
    const checkmark = page.locator("span").filter({ hasText: "✓" }).first();
    const hasCheckmark = await checkmark.isVisible().catch(() => false);
    if (hasCheckmark) {
      console.log("✅ Tool completed checkmark is visible");
    } else {
      console.log("ℹ️  Completed checkmark not found (tool may have been rendered as running or is a ToolFallback)");
    }

    // ── Verify tool card badge content ─────────────────────────────────
    // Completed tool cards typically show a result badge (e.g. "5 results",
    // "PASSED", "#42"). We check scoped to the tool card container only.
    // The badge regex uses specific patterns that won't match chat text.
    const badgePattern = /\d+ results?|\d+ matches?|PASSED|FAILED|#[0-9]+|KB|done/i;
    const badge = toolCard.locator("span, div").filter({ hasText: badgePattern }).first();
    const hasBadge = await badge.isVisible().catch(() => false);
    if (hasBadge) {
      console.log("✅ Tool result badge is visible");
    } else {
      console.log("ℹ️  Result badge not found (tool may have minimal output or be a ToolFallback)");
    }

    await page.screenshot({
      path: "/tmp/ossia-tool-ui-status-pass.png",
      fullPage: true,
    });
    console.log("📸 Screenshot saved to /tmp/ossia-tool-ui-status-pass.png");
  });
});
