import { expect, test } from "@playwright/test";

const stats = {
  documents_total: 4,
  documents_enabled: 3,
  chunks_total: 120,
  conversations_total: 2,
  qdrant_points: 118,
  by_category: [
    { category: "statute", count: 2 },
    { category: "constitutional_law", count: 1 },
  ],
  last_sync: {
    sync_run_id: "run-prev",
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:00:05Z",
    status: "completed",
    scanned_count: 4,
    changed_count: 1,
    unchanged_count: 3,
    failed_count: 0,
  },
};

const config = {
  embedding_backend: "ollama",
  embedding_model: "nomic-embed-text",
  embedding_dim: 768,
  llm_model: "mistral",
  generator_backend: "ollama",
  reranker_backend: "minilm",
  qdrant_collection: "ph_law",
  qdrant_url: "http://localhost:6333",
  ollama_base_url: "http://localhost:11434",
  chunk_size: 256,
  chunk_overlap: 32,
  min_chunks_for_answer: 1,
  max_conversation_turns: 5,
  router_enabled: false,
  edge_expansion_enabled: true,
  answerability_gate_enabled: false,
  enable_query_rewriting: true,
  faithfulness_selfcheck_enabled: false,
  later_enacted_preference_enabled: false,
  aws_region: "us-east-1",
};

const health = {
  status: "ok",
  qdrant: true,
  ollama: true,
  generator_backend: "ollama",
};

test("dashboard renders stats, health, config, and qdrant unavailable path", async ({ page }) => {
  await page.route(/\/api\/stats\/overview$/, (route) => route.fulfill({ json: stats }));
  await page.route(/\/api\/config$/, (route) => route.fulfill({ json: config }));
  await page.route(/\/api\/health$/, (route) => route.fulfill({ json: health }));

  await page.goto("/dashboard");

  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("3/4")).toBeVisible();
  await expect(page.getByText("120")).toBeVisible();
  await expect(page.getByText("Config Summary")).toBeVisible();
  await expect(page.getByText("Generator").first()).toBeVisible();
  await expect(page.getByText("By Category")).toBeVisible();

  await page.route(/\/api\/stats\/overview$/, (route) =>
    route.fulfill({ json: { ...stats, qdrant_points: null } }),
  );
  await page.reload();

  await expect(page.getByText("Qdrant unavailable")).toBeVisible();
});

test("ingestion run sync watches running row through completion", async ({ page }) => {
  let runsCalls = 0;
  await page.route(/\/api\/sync\/runs$/, (route) => {
    runsCalls += 1;
    const running = {
      sync_run_id: "run-1",
      started_at: "2026-01-01T00:00:00Z",
      completed_at: null,
      status: "running",
      scanned_count: 0,
      changed_count: 0,
      unchanged_count: 0,
      failed_count: 0,
    };
    const completed = { ...running, completed_at: "2026-01-01T00:00:05Z", status: "completed" };
    route.fulfill({ json: { runs: [runsCalls < 3 ? running : completed] } });
  });
  await page.route(/\/api\/documents\/sync$/, (route) =>
    route.fulfill({ json: { status: "sync started", sync_run_id: "run-1" } }),
  );

  await page.goto("/ingestion");
  await page.getByRole("button", { name: "Run sync" }).click();

  await expect(page.getByRole("button", { name: "Syncing…" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Run sync" })).toBeEnabled({ timeout: 6000 });
  await expect(page.getByText("completed", { exact: true })).toBeVisible();
});
