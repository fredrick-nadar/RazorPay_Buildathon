import { expect, test } from "@playwright/test";

test("landing renders at /", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /every financial record/i }),
  ).toBeVisible();
  await expect(page.locator(".badge")).toContainText(/merchant reconciliation/i);
});

test("control room renders at /dashboard", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(
    page.getByRole("heading", { name: "ARGUS CONTROL" }),
  ).toBeVisible();
  await expect(page.getByText(/synthetic data only/i)).toBeVisible();
});
