import { expect, test } from "@playwright/test";
import type { EvalDiff, EvalRunDetail } from "@/api/client";

const candidateTag = "mistral_candidate_20260708_010000";
const baselineTag = "mistral_baseline_20260708_000000";

const detail: EvalRunDetail = {
  tag: candidateTag,
  model: "mistral",
  label: "candidate",
  date: "2026-07-08",
  git_sha: "abc123",
  question_count: 2,
  scored_count: 1,
  summary: {
    overall: {
      faithfulness: 0.9,
      answer_relevancy: 0.82,
      context_precision: 0.75,
      context_recall: 0.7,
    },
    abstention: { correct: 1, total: 2, accuracy: 0.5 },
    by_category: {
      civil: {
        n: 1,
        faithfulness: 0.9,
        answer_relevancy: 0.82,
        context_precision: 0.75,
        context_recall: 0.7,
      },
    },
  },
  meta: null,
};

const diff: EvalDiff = {
  candidate_tag: candidateTag,
  baseline_tag: baselineTag,
  overall: {
    candidate: detail.summary!.overall,
    baseline: {
      faithfulness: 0.8,
      answer_relevancy: 0.8,
      context_precision: 0.7,
      context_recall: 0.65,
    },
    delta: {
      faithfulness: 0.1,
      answer_relevancy: 0.02,
      context_precision: 0.05,
      context_recall: 0.05,
    },
  },
  abstention: { candidate: 0.5, baseline: 0.25, delta: 0.25 },
  by_category: {
    civil: {
      status: "matched",
      candidate: detail.summary!.by_category.civil,
      baseline: {
        faithfulness: 0.8,
        answer_relevancy: 0.8,
        context_precision: 0.7,
        context_recall: 0.65,
      },
      delta: {
        faithfulness: 0.1,
        answer_relevancy: 0.02,
        context_precision: 0.05,
        context_recall: 0.05,
      },
    },
    criminal: {
      status: "missing_baseline",
      candidate: {
        faithfulness: 0.7,
        answer_relevancy: 0.6,
        context_precision: 0.5,
        context_recall: 0.4,
      },
      baseline: null,
      delta: null,
    },
  },
};

test("eval list links to detail with metrics, rows, and diff", async ({ page }) => {
  await page.route(/\/api\/evals\/runs$/, async (route) => {
    await route.fulfill({
      json: {
        runs: [
          {
            tag: candidateTag,
            date: "2026-07-08",
            model: "mistral",
            label: "candidate",
            questions: 2,
            scored: 1,
            holdout: false,
            git_sha: "abc123",
            abstention_accuracy: 0.5,
            faithfulness: 0.9,
            answer_relevancy: 0.82,
            context_precision: 0.75,
            context_recall: 0.7,
          },
          {
            tag: baselineTag,
            date: "2026-07-08",
            model: "mistral",
            label: "baseline",
            questions: 2,
            scored: 1,
            holdout: false,
            git_sha: "def456",
            abstention_accuracy: 0.25,
            faithfulness: 0.8,
            answer_relevancy: 0.8,
            context_precision: 0.7,
            context_recall: 0.65,
          },
        ],
      },
    });
  });
  await page.route(new RegExp(`/api/evals/runs/${candidateTag}$`), async (route) => {
    await route.fulfill({ json: detail });
  });
  await page.route(new RegExp(`/api/evals/runs/${candidateTag}/rows$`), async (route) => {
    await route.fulfill({
      json: {
        tag: candidateTag,
        row_count: 2,
        scored_count: 1,
        rows: [
          {
            eval_id: "row-1",
            question: "What is civil obligation?",
            answer: "An obligation is juridical necessity.",
            category: "civil",
            abstained: false,
            ground_truth: "Civil Code definition.",
            contexts: ["Civil Code Article 1156 context"],
            faithfulness: 0.9,
            answer_relevancy: 0.82,
            context_precision: 0.75,
            context_recall: 0.7,
            split: "regression",
            topic: "obligations",
            facet: null,
            profile: "default",
            generator_model: "haiku",
            elapsed_s: 2.4,
            expected_sources: ["ra_9262"],
            retrieved_sources: ["civil_code"],
            cited_sources: ["civil_code"],
            expected_missing: ["ra_9262"],
            selected_chunk_ids: ["chunk-1", "chunk-ghost"],
            evidence: { verdict: "insufficient", method: "heuristic", missing_facets: ["ra_9262_penalty"], detail: null },
            corrective_retrieval: { enabled: true, fired: true, added_chunks: 2 },
            model_choice: { model: "haiku", reason: "default" },
            debug_stages: [
              { name: "hybrid_retriever", out_n: 10, ms: 12.3 },
              { name: "rerank", in_n: 10, out_n: 5, ms: 45.1 },
            ],
          },
          {
            eval_id: "row-2",
            question: "Unanswerable question?",
            answer: "",
            category: "abstention",
            abstained: true,
            ground_truth: "Should abstain.",
            contexts: [],
            faithfulness: null,
            answer_relevancy: null,
            context_precision: null,
            context_recall: null,
            split: null,
            topic: null,
            facet: null,
            profile: null,
            generator_model: null,
            elapsed_s: null,
            expected_sources: [],
            retrieved_sources: [],
            cited_sources: [],
            expected_missing: [],
            selected_chunk_ids: [],
            evidence: null,
            corrective_retrieval: null,
            model_choice: null,
            debug_stages: [],
          },
        ],
      },
    });
  });
  await page.route(new RegExp(`/api/evals/runs/${candidateTag}/diff\\?baseline=.*`), async (route) => {
    await route.fulfill({ json: diff });
  });
  await page.route(/\/api\/chunks\/lookup$/, async (route) => {
    await route.fulfill({
      json: {
        chunks: [
          {
            chunk_id: "chunk-1",
            doc_id: "civil_code",
            chunk_index: 3,
            text: "Obligations arise from law, contracts, quasi-contracts...",
            char_count: 58,
            title: "Civil Code of the Philippines",
          },
        ],
        missing: ["chunk-ghost"],
      },
    });
  });
  await page.route(new RegExp(`/api/evals/runs/${candidateTag}/logs.*`), async (route) => {
    await route.fulfill({
      json: {
        tag: candidateTag,
        window: { started_at: "2026-07-08T01:00:00+08:00", completed_at: "2026-07-08T02:00:00+08:00" },
        entries: [
          { timestamp: "2026-07-08T01:00:00Z", level: "info", event: "run started", logger: "app.evals" },
          { timestamp: "2026-07-08T01:30:00Z", level: "warning", event: "retry", logger: "app.evals" },
        ],
        count: 2,
        truncated: false,
        holdout_redacted: false,
      },
    });
  });

  await page.goto("/evals");
  await expect(page.getByRole("heading", { name: "Evaluations" })).toBeVisible();
  await expect(page.getByText("Quality bands")).toBeVisible();
  await expect(page.getByText("Latest run")).toBeVisible();
  await expect(page.getByText("candidate").first()).toBeVisible();
  await page.getByRole("button", { name: /^candidate/ }).click();

  await expect(page.getByRole("heading", { name: candidateTag })).toBeVisible();
  await expect(page.getByText("0.900").first()).toBeVisible();
  await expect(page.getByText("civil").first()).toBeVisible();

  await expect(page.getByText("What is civil obligation?")).toBeVisible();
  await expect(page.getByText("Unanswerable question?")).toBeVisible();
  await expect(page.getByText("2 of 2 rows")).toBeVisible();

  await page.getByRole("button", { name: "Source miss", exact: true }).click();
  await expect(page.getByText("What is civil obligation?")).toBeVisible();
  await expect(page.getByText("Unanswerable question?")).not.toBeVisible();
  await expect(page.getByText("1 of 2 rows")).toBeVisible();
  await page.getByRole("button", { name: "Source miss", exact: true }).click();

  await page.getByRole("combobox", { name: "Evidence verdict" }).click();
  await page.getByRole("option", { name: "insufficient" }).click();
  await expect(page.getByText("What is civil obligation?")).toBeVisible();
  await expect(page.getByText("Unanswerable question?")).not.toBeVisible();
  await expect(page.getByText("1 of 2 rows")).toBeVisible();
  await page.getByRole("combobox", { name: "Evidence verdict" }).click();
  await page.getByRole("option", { name: "all verdicts" }).click();

  await page.getByText("What is civil obligation?").click();
  await expect(page.getByRole("heading", { name: "row-1" })).toBeVisible();
  await expect(page.getByText("hybrid_retriever")).toBeVisible();
  await expect(page.getByText("rerank")).toBeVisible();
  await expect(page.getByText("ra_9262", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Civil Code of the Philippines")).toBeVisible();
  await expect(page.getByText(/Stale chunk IDs.*chunk-ghost/)).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByText("Unanswerable question?").click();
  await expect(page.getByRole("heading", { name: "row-2" })).toBeVisible();
  await expect(page.getByText("Pipeline stages")).not.toBeVisible();
  await page.keyboard.press("Escape");

  await expect(page.getByText("run started")).toBeVisible();
  await expect(page.getByText(/Window: 2026-07-08T01:00:00\+08:00/)).toBeVisible();

  await page.getByRole("combobox", { name: "Baseline" }).click();
  await page.getByRole("option", { name: "baseline · 2026-07-08" }).click();
  await expect(page.getByText("+0.100", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("missing_baseline")).toBeVisible();
  await expect(page.getByRole("cell", { name: "n/a" }).first()).toBeVisible();
});
