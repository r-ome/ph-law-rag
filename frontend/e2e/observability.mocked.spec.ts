import { expect, test } from "@playwright/test";
import type { TraceRecord } from "@/api/client";

const fullTrace: TraceRecord = {
  trace_id: "trace-1",
  trace_label: "lab",
  timestamp: "2026-07-08T00:00:00Z",
  session_id: null,
  question: "What are penalties for theft?",
  rewritten_question: "What are penalties for theft?",
  stage_counts: { retrieved: 2, pre_expansion: 1, selected: 1 },
  retrieved_chunks: [
    {
      chunk_id: "chunk-1",
      score: 0.93,
      source_id: "rpc_1930",
      unit_label: "Article 309",
      provision_id: "rpc:309",
      expanded_from_parent: true,
      consolidated: "",
      dedup_merged_chunk_ids: [],
      preview: "Theft penalties...",
      text: "Theft penalties depend on the value of the property and other circumstances.",
    },
  ],
  pre_expansion_chunks: [],
  selected_chunks: [
    {
      chunk_id: "chunk-1",
      score: 0.93,
      source_id: "rpc_1930",
      unit_label: "Article 309",
      provision_id: "rpc:309",
      expanded_from_parent: true,
      consolidated: "",
      dedup_merged_chunk_ids: [],
      preview: "Theft penalties...",
      text: "Theft penalties depend on the value of the property and other circumstances.",
    },
  ],
  retrieval_strategy: { strategy: "default", knobs: {} },
  intent_router: { enabled: false, model: null, decision: null },
  feature_flags: { trace_logging_enabled: true },
  abstained: false,
  error: false,
  stages: [],
  latency_ms: 99,
  prompt_length: 300,
  generator_model: "mistral",
};

test("observability opens trace details and logs filter re-queries", async ({ page }) => {
  let logsUrl = "";
  await page.route(/\/api\/traces(\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        traces: [
          {
            trace_id: "trace-1",
            timestamp: "2026-07-08T00:00:00Z",
            trace_label: "lab",
            question: "What are penalties for theft?",
            strategy: "default",
            stage_counts: { retrieved: 2, pre_expansion: 1, selected: 1 },
            latency_ms: 99,
            abstained: false,
            error: false,
          },
          {
            trace_id: "trace-2",
            timestamp: "2026-07-07T00:00:00Z",
            trace_label: "eval",
            question: "What is data privacy?",
            strategy: "current_law",
            stage_counts: { retrieved: 3, pre_expansion: 2, selected: 1 },
            latency_ms: 120,
            abstained: false,
            error: false,
          },
        ],
      },
    }),
  );
  await page.route(/\/api\/traces\/trace-1$/, (route) => route.fulfill({ json: fullTrace }));
  await page.route(/\/api\/logs(\?.*)?$/, (route) => {
    logsUrl = route.request().url();
    route.fulfill({
      json: {
        entries: [
          {
            timestamp: "2026-07-08T00:00:00Z",
            level: "warning",
            event: "generation_failed",
            logger: "app.retriever",
            raw: null,
          },
        ],
        count: 1,
      },
    });
  });

  await page.goto("/observability");
  await expect(page.getByText("What are penalties for theft?")).toBeVisible();
  await page.getByText("What are penalties for theft?").click();
  await expect(page.getByRole("heading", { name: "Selected" })).toBeVisible();
  await expect(page.getByText("expanded_from_parent").first()).toBeVisible();

  await page.goto("/logs");
  await expect(page.getByText("generation_failed")).toBeVisible();
  await page.getByRole("combobox", { name: "Log level" }).click();
  await page.getByRole("option", { name: "warning" }).click();
  await expect.poll(() => logsUrl).toContain("level=warning");
});
