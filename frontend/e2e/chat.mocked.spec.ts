import { expect, test } from "@playwright/test";
import type {
  ChatSource,
  ConversationSummary,
  ConversationTurn,
} from "@/api/client";

const source: ChatSource = {
  ref: 1,
  title: "Revised Penal Code",
  url: "https://example.com/rpc",
  source_id: "rpc_1930",
  locator: "Article 309",
  via: null,
};

const conversation: ConversationSummary = {
  session_id: "sess-1",
  title: "Theft...",
  created_at: "2026-07-08T00:00:00Z",
  turn_count: 1,
};

const turn: ConversationTurn = {
  turn_index: 0,
  question: "What are the penalties for theft?",
  answer: "Theft is punished [1].",
  sources: [source],
};

test("chat sends a question and replays citations from mocked API", async ({ page }) => {
  await page.route(/\/api\/query\/ask$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 100));
    await route.fulfill({
      json: {
        answer: "Theft is punished [1].",
        sources: [source],
        abstained: false,
        error: false,
        session_id: "sess-1",
      },
    });
  });
  await page.route(/\/api\/conversations$/, (route) =>
    route.fulfill({ json: { conversations: [conversation] } }),
  );
  await page.route(/\/api\/conversations\/sess-1$/, (route) =>
    route.fulfill({
      json: {
        session_id: "sess-1",
        turn_count: 1,
        turns: [turn],
      },
    }),
  );

  await page.goto("/chat");

  await page.getByPlaceholder("Ask a legal question…").fill("What are the penalties for theft?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Thinking…")).toBeVisible();
  await expect(page).toHaveURL(/\/chat\/sess-1$/);
  await expect(page.getByText("Theft is punished")).toBeVisible();

  await page.getByRole("button", { name: "Citation 1" }).click();
  await expect(page.locator("#src-0-1")).toContainText("[1] Revised Penal Code");
  await expect(page.locator("#src-0-1")).toContainText("Article 309");

  await page.goto("/chat/sess-1");
  await expect(page.getByText("What are the penalties for theft?")).toBeVisible();
  await expect(page.getByRole("button", { name: "Citation 1" })).toBeVisible();
  await expect(page.locator("#src-0-1")).toContainText("Revised Penal Code");
});
