import { expect, test } from "@playwright/test";

test("eval run triage table renders real rows and a row's pipeline stages", async ({ page }) => {
  await page.goto("/evals");

  const runRows = page.locator("table tbody tr");
  await expect(runRows.first()).toBeVisible();

  // Pick the newest non-holdout run (holdout runs render a redaction notice, not rows).
  const count = await runRows.count();
  let opened = false;
  for (let i = 0; i < count; i++) {
    await runRows.nth(i).click();
    const redacted = page.getByText("Holdout run — per-row data is redacted.");
    if (await redacted.isVisible().catch(() => false)) {
      await page.goBack();
      continue;
    }
    opened = true;
    break;
  }
  expect(opened).toBe(true);

  const rowsCard = page
    .locator('[data-slot="card"]')
    .filter({ has: page.getByText("Per-question rows", { exact: true }) });
  const rows = rowsCard.getByRole("table").locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThan(0);

  await rows.first().click();
  await expect(page.getByText("Pipeline stages")).toBeVisible();
});
