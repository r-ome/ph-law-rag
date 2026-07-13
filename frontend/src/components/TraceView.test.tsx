import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import TraceView from "@/components/TraceView";
import type { TraceRecord } from "@/api/client";

const trace: TraceRecord = {
  trace_id: "trace-1",
  trace_label: "lab",
  timestamp: "2026-07-08T00:00:00Z",
  session_id: null,
  question: "What are the penalties for theft?",
  rewritten_question: "What are the penalties for theft?",
  stage_counts: { retrieved: 2, pre_expansion: 2, selected: 1 },
  retrieved_chunks: [
    {
      chunk_id: "c1",
      score: 0.91234,
      source_id: "rpc_1930",
      unit_label: "Article 309",
      provision_id: "rpc:309",
      expanded_from_parent: true,
      consolidated: "",
      dedup_merged_chunk_ids: ["c1a"],
      preview: "Theft is punished by...",
      text: "Theft is punished by graduated penalties depending on the value of the property taken.",
    },
    {
      chunk_id: "c2",
      score: 0.81234,
      source_id: "rpc_1930",
      unit_label: "Article 308",
      provision_id: "rpc:308",
      expanded_from_parent: false,
      consolidated: "",
      dedup_merged_chunk_ids: [],
      preview: "Who are liable for theft...",
      text: "Who are liable for theft under Article 308 of the Revised Penal Code.",
    },
  ],
  pre_expansion_chunks: [],
  selected_chunks: [
    {
      chunk_id: "c1",
      score: 0.91234,
      source_id: "rpc_1930",
      unit_label: "Article 309",
      provision_id: "rpc:309",
      expanded_from_parent: true,
      consolidated: "",
      dedup_merged_chunk_ids: ["c1a"],
      preview: "Theft is punished by...",
      text: "Theft is punished by graduated penalties depending on the value of the property taken.",
    },
  ],
  retrieval_strategy: { strategy: "default", knobs: {} },
  intent_router: { enabled: false, model: null, decision: null },
  feature_flags: { trace_logging_enabled: true },
  policy: {},
  abstained: false,
  error: false,
  stages: [],
  latency_ms: 123.45,
  prompt_length: 500,
  generator_model: "mistral",
};

test("renders trace columns, scores, source ids, and expansion badges", () => {
  render(<TraceView trace={trace} />);

  expect(screen.getByRole("heading", { name: "Retrieved" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Reranked" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Selected" })).toBeInTheDocument();
  expect(screen.getAllByText("0.9123").length).toBeGreaterThan(0);
  expect(screen.getAllByText("rpc_1930").length).toBeGreaterThan(0);
  expect(screen.getAllByText("expanded_from_parent").length).toBeGreaterThan(0);
});

test("expands a chunk card to show the full retrieved text", async () => {
  const user = userEvent.setup();
  render(<TraceView trace={trace} />);

  expect(screen.queryByText(/graduated penalties depending/)).not.toBeInTheDocument();

  const buttons = screen.getAllByRole("button", { name: "Read more" });
  expect(buttons.length).toBeGreaterThan(0);
  const firstButton = buttons[0];
  if (!firstButton) throw new Error("Read more button not found");
  await user.click(firstButton);

  expect(screen.getAllByText(/graduated penalties depending/).length).toBeGreaterThan(0);
  expect(screen.getAllByRole("button", { name: "Show less" }).length).toBeGreaterThan(0);
});
