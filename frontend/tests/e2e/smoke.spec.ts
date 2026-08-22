import { expect, test } from "@playwright/test";

test("control room placeholder renders", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "ARGUS CONTROL" }),
  ).toBeVisible();
  await expect(page.getByText(/synthetic data only/i)).toBeVisible();
});
