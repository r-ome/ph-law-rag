import { expect, test } from "vitest";
import type { EvalRow } from "@/api/client";
import { defaultRowFilters, filterRows } from "@/lib/evalRows";

function row(overrides: Partial<EvalRow> = {}): EvalRow {
  return {
    eval_id: "eval_001",
    question: "What is due process?",
    answer: "",
    category: "civil",
    abstained: false,
    ground_truth: null,
    contexts: [],
    faithfulness: null,
    answer_relevancy: null,
    context_precision: null,
    context_recall: null,
    split: "regression",
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
    ...overrides,
  };
}

test("filterRows: search matches question case-insensitively", () => {
  const rows = [row({ question: "Due Process Clause" }), row({ question: "Ownership rules" })];
  const result = filterRows(rows, { ...defaultRowFilters, search: "due process" });
  expect(result).toHaveLength(1);
  expect(result[0]!.question).toBe("Due Process Clause");
});

test("filterRows: search matches eval_id case-insensitively", () => {
  const rows = [row({ eval_id: "EVAL_042" }), row({ eval_id: "eval_001" })];
  const result = filterRows(rows, { ...defaultRowFilters, search: "eval_042" });
  expect(result).toHaveLength(1);
  expect(result[0]!.eval_id).toBe("EVAL_042");
});

test("filterRows: verdict none keeps only rows with evidence null", () => {
  const rows = [
    row({ evidence: { verdict: "sufficient", method: null, missing_facets: [], detail: null } }),
    row({ evidence: null }),
  ];
  const result = filterRows(rows, { ...defaultRowFilters, verdict: "none" });
  expect(result).toHaveLength(1);
  expect(result[0]!.evidence).toBeNull();
});

test("filterRows: verdict specific value matches evidence.verdict", () => {
  const rows = [
    row({ evidence: { verdict: "insufficient", method: null, missing_facets: [], detail: null } }),
    row({ evidence: { verdict: "sufficient", method: null, missing_facets: [], detail: null } }),
  ];
  const result = filterRows(rows, { ...defaultRowFilters, verdict: "insufficient" });
  expect(result).toHaveLength(1);
});

test("filterRows: sourceMissOnly keeps only non-empty expected_missing", () => {
  const rows = [row({ expected_missing: ["ra_9262"] }), row({ expected_missing: [] })];
  const result = filterRows(rows, { ...defaultRowFilters, sourceMissOnly: true });
  expect(result).toHaveLength(1);
  expect(result[0]!.expected_missing).toEqual(["ra_9262"]);
});

test("filterRows: filters compose with AND", () => {
  const rows = [
    row({ category: "civil", abstained: true, expected_missing: ["a"] }),
    row({ category: "civil", abstained: false, expected_missing: ["a"] }),
    row({ category: "criminal", abstained: true, expected_missing: ["a"] }),
  ];
  const result = filterRows(rows, {
    ...defaultRowFilters,
    category: "civil",
    abstainedOnly: true,
    sourceMissOnly: true,
  });
  expect(result).toHaveLength(1);
  expect(result[0]!.category).toBe("civil");
  expect(result[0]!.abstained).toBe(true);
});
