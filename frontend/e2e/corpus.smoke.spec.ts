import { expect, test } from "@playwright/test";

test("corpus browser loads real data from the backend", async ({ page }) => {
  await page.goto("/");

  const firstLink = page.getByRole("table").getByRole("link").first();
  await expect(firstLink).toBeVisible();
  const title = await firstLink.textContent();

  await firstLink.click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText(title ?? "");
  await expect(page.getByText("No normalized text.")).toHaveCount(0);
});
