import { STORAGE_KEYS } from "../src/constants";
import { test, expect } from "@playwright/test";

const { ACTIVE_PANEL } = STORAGE_KEYS;

/**
 * Returns the text content of the currently active panel tab.
 * The active tab is identified by the `text-white` class.
 * Throws if no tab is found, making failures obvious.
 */
async function getActiveTab(page: import("@playwright/test").Page) {
  const active = await page.evaluate(() => {
    const tabs = document.querySelectorAll("nav button");
    for (const t of tabs) {
      if (t.className.includes("text-white")) {
        return t.textContent?.trim() ?? null;
      }
    }
    return null;
  });
  if (active === null) {
    throw new Error("Could not find active tab — no tab has the text-white class");
  }
  return active;
}

async function reloadAndWait(page: import("@playwright/test").Page) {
  await page.reload({ waitUntil: "networkidle" });
  // Wait for React to re-hydrate and render the header
  await page.waitForSelector("header");
  await page.waitForTimeout(200);
}

test.describe("Panel persistence", () => {
  test("defaults to Chat on first visit", async ({ page }) => {
    // Pass the key as an argument so it's available in the browser context
    await page.addInitScript(
      (key: string) => { localStorage.removeItem(key); },
      ACTIVE_PANEL,
    );

    await page.goto("/", { waitUntil: "networkidle" });
    await page.waitForSelector("header");

    const active = await getActiveTab(page);
    expect(active).toMatch(/^Chat/);
  });

  test("persists Tools tab across refresh", async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });
    await page.waitForSelector("header");

    // Click the Tools tab (3rd nav button — index 2)
    const tabs = page.locator("nav button");
    await tabs.nth(2).click();
    await page.waitForTimeout(200);

    // Verify Tools is now active
    let active = await getActiveTab(page);
    expect(active).toMatch(/^Tools/);

    // Verify localStorage was written — pass key as argument
    const stored = await page.evaluate(
      (key: string) => localStorage.getItem(key),
      ACTIVE_PANEL,
    );
    expect(stored).toBe("tools");

    // Refresh and verify persistence
    await reloadAndWait(page);
    active = await getActiveTab(page);
    expect(active).toMatch(/^Tools/);
  });

  test("persists Subagents tab across refresh", async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });
    await page.waitForSelector("header");

    // Click the Subagents tab (2nd nav button — index 1)
    const tabs = page.locator("nav button");
    await tabs.nth(1).click();
    await page.waitForTimeout(200);

    // Verify Subagents is active
    let active = await getActiveTab(page);
    expect(active).toMatch(/^Subagents/);

    // Refresh and verify persistence
    await reloadAndWait(page);
    active = await getActiveTab(page);
    expect(active).toMatch(/^Subagents/);
  });

  test("persists across multiple tab switches", async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });
    await page.waitForSelector("header");

    const tabs = page.locator("nav button");

    // Switch: Chat → Tools → refresh → ReAct → refresh → Chat → refresh
    await tabs.nth(2).click(); // Tools
    await reloadAndWait(page);
    expect(await getActiveTab(page)).toMatch(/^Tools/);

    await tabs.nth(3).click(); // ReAct
    await reloadAndWait(page);
    expect(await getActiveTab(page)).toMatch(/^ReAct/);

    await tabs.nth(0).click(); // Chat
    await reloadAndWait(page);
    expect(await getActiveTab(page)).toMatch(/^Chat/);
  });

  test("falls back to Chat when localStorage has invalid value", async ({
    page,
  }) => {
    // Pass key + value as arguments to the browser context
    await page.addInitScript(
      (key: string) => { localStorage.setItem(key, "invalid-panel"); },
      ACTIVE_PANEL,
    );

    await page.goto("/", { waitUntil: "networkidle" });
    await page.waitForSelector("header");

    const active = await getActiveTab(page);
    expect(active).toMatch(/^Chat/);
  });
});
