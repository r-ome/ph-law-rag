import { expect, test } from "@playwright/test";
import type { TraceRecord } from "@/api/client";

const trace: TraceRecord = {
  trace_id: "trace-lab",
  trace_label: "lab",
  timestamp: "2026-07-08T00:00:00Z",
  session_id: null,
  question: "What are the penalties for theft?",
  rewritten_question: "What are the penalties for theft?",
  stage_counts: { retrieved: 1, pre_expansion: 1, selected: 1 },
  retrieved_chunks: [
    {
      chunk_id: "chunk-1",
      score: 0.9234,
      source_id: "rpc_1930",
      unit_label: "Article 309",
      provision_id: "rpc:309",
      expanded_from_parent: false,
      consolidated: "",
      dedup_merged_chunk_ids: [],
      preview: "The penalty for theft depends on value...",
      text: "The penalty for theft depends on value and the circumstances described in Article 309.",
    },
  ],
  pre_expansion_chunks: [
    {
      chunk_id: "chunk-1",
      score: 0.9234,
      source_id: "rpc_1930",
      unit_label: "Article 309",
      provision_id: "rpc:309",
      expanded_from_parent: false,
      consolidated: "",
      dedup_merged_chunk_ids: [],
      preview: "The penalty for theft depends on value...",
      text: "The penalty for theft depends on value and the circumstances described in Article 309.",
    },
  ],
  selected_chunks: [
    {
      chunk_id: "chunk-1",
      score: 0.9234,
      source_id: "rpc_1930",
      unit_label: "Article 309",
      provision_id: "rpc:309",
      expanded_from_parent: false,
      consolidated: "",
      dedup_merged_chunk_ids: [],
      preview: "The penalty for theft depends on value...",
      text: "The penalty for theft depends on value and the circumstances described in Article 309.",
    },
  ],
  retrieval_strategy: { strategy: "current_law", knobs: {} },
  intent_router: { enabled: true, model: "rules", decision: { strategy: "current_law" } },
  feature_flags: { trace_logging_enabled: true },
  abstained: false,
  error: false,
  stages: [],
  latency_ms: 88.8,
  prompt_length: 420,
  generator_model: "mistral",
};

test("lab runs retrieval, renders trace, and sends strategy override", async ({ page }) => {
  let posted: unknown;
  await page.route(/\/api\/retrieval\/inspect$/, async (route) => {
    posted = route.request().postDataJSON();
    await route.fulfill({
      json: {
        answer: "Theft penalties are governed by Article 309 [1].",
        sources: [
          {
            ref: 1,
            title: "Revised Penal Code",
            url: "https://example.com/rpc",
            source_id: "rpc_1930",
            locator: "Article 309",
            via: null,
          },
        ],
        abstained: false,
        error: false,
        error_message: null,
        trace,
      },
    });
  });

  await page.goto("/lab");
  await page.getByPlaceholder("Ask a legal question...").fill("What are the penalties for theft?");
  await page.getByRole("combobox", { name: "Strategy" }).click();
  await page.getByRole("option", { name: "current_law" }).click();
  await page.getByRole("button", { name: "Run" }).click();

  await expect(page.getByText("Theft penalties are governed")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Retrieved" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Selected" })).toBeVisible();
  await expect(page.getByText("0.9234").first()).toBeVisible();
  expect(posted).toMatchObject({ question: "What are the penalties for theft?", strategy: "current_law" });
});
