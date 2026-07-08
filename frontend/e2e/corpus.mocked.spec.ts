import { expect, test } from "@playwright/test";
import type {
  ChunkListResponse,
  DocumentDetail,
  DocumentSummary,
} from "@/api/client";

const civilDocument: DocumentSummary = {
  doc_id: "civil-code",
  source_id: "civil_code",
  title: "Civil Code of the Philippines",
  url: "https://example.com/civil-code",
  doc_type: "code",
  category: "civil",
  enabled: true,
  updated_at: "2026-01-01T00:00:00",
  last_fetched: "2026-01-02T00:00:00",
  chunk_count: 12,
  status: "operative",
  source_index: "lawphil",
  official_number: "RA 386",
  tags: ["civil-law", "obligations"],
};

const familyDocument: DocumentSummary = {
  doc_id: "family-code",
  source_id: "family_code",
  title: "Family Code of the Philippines",
  url: "https://example.com/family-code",
  doc_type: "code",
  category: "family",
  enabled: true,
  updated_at: "2026-01-01T00:00:00",
  last_fetched: "2026-01-02T00:00:00",
  chunk_count: 8,
  status: "operative",
  source_index: "lawphil",
  official_number: "EO 209",
  tags: ["family-law"],
};

const documents: DocumentSummary[] = [civilDocument, familyDocument];

const detail: DocumentDetail = {
  ...civilDocument,
  normalized_text: "Article 1. This Act shall be known as the Civil Code of the Philippines.",
  content_hash: "abc123",
  content_length: 72,
  extraction_method: "html",
  http_status: 200,
  approval_date: "1949-06-18",
  effectivity_date: "1950-08-30",
  availability: "official",
  structure: "articles",
  notes: null,
  amends: ["old_civil_code"],
  repeals: [],
  supersedes: [],
  implements: [],
  amends_namespace: "ph_law",
};

const chunks: ChunkListResponse = {
  doc_id: "civil-code",
  chunk_count: 1,
  chunks: [
    {
      chunk_id: "civil-code-0001",
      chunk_index: 0,
      text: "Article 1. This Act shall be known as the Civil Code of the Philippines.",
      char_count: 72,
      token_estimate: 18,
      qdrant_id: "civil-code-0001",
    },
  ],
};

test("corpus browser list, filters, detail, and chunks use mocked API", async ({ page }) => {
  await page.route(/\/api\/documents$/, (route) =>
    route.fulfill({ json: { documents } }),
  );
  await page.route(/\/api\/documents\/civil-code$/, (route) =>
    route.fulfill({ json: detail }),
  );
  await page.route(/\/api\/documents\/civil-code\/chunks$/, (route) =>
    route.fulfill({ json: chunks }),
  );

  await page.goto("/");

  await expect(page.getByRole("link", { name: "Civil Code of the Philippines" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Family Code of the Philippines" })).toBeVisible();
  await expect(page.getByText("2 of 2 documents")).toBeVisible();

  await page.getByPlaceholder("Search title or tags…").fill("family");
  await expect(page.getByRole("link", { name: "Family Code of the Philippines" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Civil Code of the Philippines" })).toHaveCount(0);
  await expect(page.getByText("1 of 2 documents")).toBeVisible();

  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("link", { name: "Civil Code of the Philippines" }).click();

  await expect(page).toHaveURL(/\/documents\/civil-code$/);
  await expect(page.getByRole("heading", { name: "Civil Code of the Philippines" })).toBeVisible();
  await expect(page.getByText("Article 1. This Act shall be known")).toBeVisible();
  await expect(page.getByText("old_civil_code")).toBeVisible();

  await page.getByRole("button", { name: "Show chunks" }).click();
  await expect(page.getByText("1 chunks")).toBeVisible();
  await expect(page.getByText("civil-code-0001")).toBeVisible();
});
