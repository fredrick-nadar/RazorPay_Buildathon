import { expect, test } from "@playwright/test";

test("landing renders at /", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /every financial record/i }),
  ).toBeVisible();
  await expect(page.locator(".badge")).toContainText(/merchant reconciliation/i);
  await expect(page.getByText(/historical frozen-holdout result/i)).toBeVisible();
  await expect(page.getByText(/deterministic fake investigator/i)).toBeVisible();
});

test("control room renders at /dashboard", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(
    page.getByRole("heading", { name: "ARGUS CONTROL" }),
  ).toBeVisible();
  await expect(page.getByText(/synthetic data only/i)).toBeVisible();
  await expect(page.getByText("Backend reachable")).toBeVisible();
});

test("empty dashboard gives one truthful path to the first run", async ({ page }) => {
  await page.route("**/api/v1/runs/active", route => route.fulfill({ status: 200, json: null }));
  await page.goto("/dashboard");

  await expect(page.getByText("No reconciliation run yet", { exact: true })).toBeVisible();
  await expect(page.getByText(/Import gateway, bank, and ledger evidence/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Import evidence" })).toBeVisible();
  // Home says what the empty database means for the copilot, without repeating
  // the banner's headline.
  await expect(page.getByText("Nothing to answer from yet")).toBeVisible();

  await page.getByRole("button", { name: "Import evidence" }).click();
  // The intake modal offers the three dashboard evidence sources and nothing else.
  await expect(page.getByText("Bank statement", { exact: true })).toBeVisible();
  await expect(page.getByText("Merchant ledger", { exact: true })).toBeVisible();
});

test("unavailable dashboard fails closed and exposes retry", async ({ page }) => {
  await page.route("**/api/v1/runs/active", route => route.abort("connectionrefused"));
  await page.goto("/dashboard");

  await expect(page.getByText("Dashboard data is temporarily unavailable", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry dashboard" })).toBeVisible();
  await expect(page.getByText("Backend unavailable", { exact: true })).toBeVisible();
});

test("active run dossier reports runtime evidence without certification claims", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Evidence Dossier" }).click();

  const dossier = page.getByRole("dialog", { name: "Run evidence dossier" });
  await expect(page.getByRole("heading", { name: "Run evidence dossier" })).toBeVisible();
  await expect(dossier.getByText("Runtime match rate", { exact: true })).toBeVisible();
  await expect(page.getByText(/not an external audit or regulatory certificate/i)).toBeVisible();
  await expect(page.getByText(/100\.0% Verified/i)).toHaveCount(0);
  await expect(page.getByText(/RBI \/ FINTECH COMPLIANT/i)).toHaveCount(0);
});
