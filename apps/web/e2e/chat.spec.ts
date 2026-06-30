/**
 * E2E smoke tests for the OpsLens AI chat interface.
 * Requires a running dev/prod server (npm run dev) and a valid BASE_URL env var.
 * These tests are NOT run in the unit-test CI job — they run as a separate E2E job
 * that provisions a full app + backend stack.
 */
import { test, expect } from "@playwright/test";

test.describe("Chat interface", () => {
  test("loads the chat page without errors", async ({ page }) => {
    await page.goto("/chat");
    // Should not show a fatal error boundary
    await expect(page.locator("body")).not.toContainText("Application error");
    await expect(page.locator("body")).not.toContainText("500");
  });

  test("shows the message input textarea", async ({ page }) => {
    await page.goto("/chat");
    const textarea = page.getByPlaceholder("Ask anything about your operations…");
    await expect(textarea).toBeVisible();
  });

  test("send button is disabled when input is empty", async ({ page }) => {
    await page.goto("/chat");
    const sendBtn = page.getByTitle("Send (Enter)");
    await expect(sendBtn).toBeDisabled();
  });

  test("send button becomes enabled after typing", async ({ page }) => {
    await page.goto("/chat");
    await page.getByPlaceholder("Ask anything about your operations…").fill("What is the error rate?");
    const sendBtn = page.getByTitle("Send (Enter)");
    await expect(sendBtn).toBeEnabled();
  });

  test("source filter chips appear when the filter button is clicked", async ({ page }) => {
    await page.goto("/chat");
    await page.getByTitle("Filter by source").click();
    await expect(page.getByText("Slack")).toBeVisible();
    await expect(page.getByText("Jira")).toBeVisible();
    await expect(page.getByText("Zendesk")).toBeVisible();
  });
});

test.describe("Insights page", () => {
  test("loads the insights page without errors", async ({ page }) => {
    await page.goto("/insights");
    await expect(page.locator("body")).not.toContainText("Application error");
  });
});
