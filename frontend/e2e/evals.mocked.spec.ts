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
          },
        ],
      },
    });
  });
  await page.route(new RegExp(`/api/evals/runs/${candidateTag}/diff\\?baseline=.*`), async (route) => {
    await route.fulfill({ json: diff });
  });

  await page.goto("/evals");
  await expect(page.getByRole("heading", { name: "Evaluations" })).toBeVisible();
  await expect(page.getByRole("link", { name: candidateTag })).toBeVisible();
  await page.getByRole("link", { name: candidateTag }).click();

  await expect(page.getByRole("heading", { name: candidateTag })).toBeVisible();
  await expect(page.getByText("0.900").first()).toBeVisible();
  await expect(page.getByText("civil")).toBeVisible();

  await page.getByRole("button", { name: "Show rows" }).click();
  await expect(page.getByText("What is civil obligation?")).toBeVisible();
  await page.getByText("What is civil obligation?").click();
  await expect(page.getByText("Civil Code Article 1156 context")).toBeVisible();
  await expect(page.getByText("Unanswerable question?")).toBeVisible();

  await page.getByRole("combobox", { name: "Baseline" }).click();
  await page.getByRole("option", { name: baselineTag }).click();
  await expect(page.getByText("+0.100").first()).toBeVisible();
  await expect(page.getByText("missing_baseline")).toBeVisible();
  await expect(page.getByRole("cell", { name: "n/a" }).first()).toBeVisible();
});
