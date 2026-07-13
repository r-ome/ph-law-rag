import { expect, test, vi } from "vitest";
import {
  ask,
  getConfig,
  getConversation,
  getDocument,
  getLogs,
  getEvalDiff,
  getEvalRows,
  getEvalRun,
  getEvalRunLogs,
  getStats,
  getTrace,
  inspectRetrieval,
  listEvalRuns,
  listDocuments,
  listTraces,
  lookupChunks,
  startSync,
} from "@/api/client";

test("listDocuments hits /api/documents and returns typed shape", async () => {
  const payload = { documents: [] };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 })));
  const result = await listDocuments();
  expect(result.documents).toEqual([]);
});

test("getDocument hits /api/documents/:id and returns typed shape", async () => {
  const payload = {
    doc_id: "abc",
    source_id: "src-abc",
    title: "Test Law",
    url: "https://example.com",
    doc_type: "statute",
    category: "civil",
    enabled: true,
    updated_at: null,
    last_fetched: null,
    chunk_count: 1,
    status: "operative",
    source_index: "lawphil",
    official_number: null,
    tags: [],
    normalized_text: "text",
    content_hash: null,
    content_length: null,
    extraction_method: null,
    http_status: null,
    approval_date: null,
    effectivity_date: null,
    availability: null,
    structure: null,
    notes: null,
    amends: [],
    repeals: [],
    supersedes: [],
    implements: [],
    amends_namespace: null,
  };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await getDocument("abc");

  expect(fetchMock).toHaveBeenCalledWith("/api/documents/abc");
  expect(result.title).toBe("Test Law");
});

test("ask posts JSON to /api/query/ask and returns typed shape", async () => {
  const payload = {
    answer: "Answer [1].",
    sources: [
      {
        ref: 1,
        title: "Source",
        url: "https://example.com",
        source_id: "source",
        locator: null,
        via: null,
      },
    ],
    abstained: false,
    error: false,
    session_id: "sess-1",
  };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await ask({ question: "x" });

  expect(fetchMock).toHaveBeenCalledWith("/api/query/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question: "x" }),
  });
  expect(result.session_id).toBe("sess-1");
});

test("getConversation hits /api/conversations/:id and returns typed shape", async () => {
  const payload = { session_id: "abc", turn_count: 0, turns: [] };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await getConversation("abc");

  expect(fetchMock).toHaveBeenCalledWith("/api/conversations/abc");
  expect(result.session_id).toBe("abc");
});

test("getStats hits /api/stats/overview", async () => {
  const payload = {
    documents_total: 1,
    documents_enabled: 1,
    chunks_total: 3,
    conversations_total: 0,
    qdrant_points: null,
    by_category: [],
    last_sync: null,
  };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await getStats();

  expect(fetchMock).toHaveBeenCalledWith("/api/stats/overview");
  expect(result.chunks_total).toBe(3);
});

test("getConfig hits /api/config", async () => {
  const payload = {
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
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await getConfig();

  expect(fetchMock).toHaveBeenCalledWith("/api/config");
  expect(result.generator_backend).toBe("ollama");
});

test("startSync posts to /api/documents/sync and returns run id", async () => {
  const payload = { status: "sync started", sync_run_id: "run-1" };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await startSync();

  expect(fetchMock).toHaveBeenCalledWith("/api/documents/sync", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({}),
  });
  expect(result.sync_run_id).toBe("run-1");
});

test("inspectRetrieval posts to /api/retrieval/inspect", async () => {
  const payload = {
    answer: "Answer",
    sources: [],
    abstained: false,
    error: false,
    error_message: null,
    trace: null,
  };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await inspectRetrieval({ question: "x" });

  expect(fetchMock).toHaveBeenCalledWith("/api/retrieval/inspect", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question: "x" }),
  });
  expect(result.answer).toBe("Answer");
});

test("listTraces builds query params", async () => {
  const payload = { traces: [] };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await listTraces({ limit: 5 });

  expect(fetchMock).toHaveBeenCalledWith("/api/traces?limit=5");
  expect(result.traces).toEqual([]);
});

test("getTrace hits /api/traces/:id", async () => {
  const payload = {
    trace_id: "t1",
    question: "x",
    rewritten_question: "x",
    stage_counts: {},
    retrieved_chunks: [],
    pre_expansion_chunks: [],
    selected_chunks: [],
    retrieval_strategy: {},
    intent_router: {},
    feature_flags: {},
    abstained: false,
    error: false,
    stages: [],
  };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await getTrace("t1");

  expect(fetchMock).toHaveBeenCalledWith("/api/traces/t1");
  expect(result.trace_id).toBe("t1");
});

test("getLogs builds level query param", async () => {
  const payload = { entries: [], count: 0 };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await getLogs({ level: "warning" });

  expect(fetchMock).toHaveBeenCalledWith("/api/logs?level=warning");
  expect(result.count).toBe(0);
});

test("eval client helpers hit eval endpoints", async () => {
  const fetchMock = vi.fn(async (path: string) => {
    if (path === "/api/evals/runs") return new Response(JSON.stringify({ runs: [] }), { status: 200 });
    if (path === "/api/evals/runs/t") return new Response(JSON.stringify({ tag: "t" }), { status: 200 });
    if (path === "/api/evals/runs/t/rows") {
      return new Response(JSON.stringify({ tag: "t", row_count: 0, scored_count: 0, rows: [] }), { status: 200 });
    }
    return new Response(
      JSON.stringify({
        candidate_tag: "a",
        baseline_tag: "b",
        overall: { candidate: {}, baseline: {}, delta: {} },
        abstention: {},
        by_category: {},
      }),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetchMock);

  await listEvalRuns();
  await getEvalRun("t");
  await getEvalRows("t");
  await getEvalDiff("a", "b");

  expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/evals/runs");
  expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/evals/runs/t");
  expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/evals/runs/t/rows");
  expect(fetchMock).toHaveBeenNthCalledWith(4, "/api/evals/runs/a/diff?baseline=b");
});

test("lookupChunks posts chunk_ids to /api/chunks/lookup", async () => {
  const payload = { chunks: [], missing: [] };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await lookupChunks(["a"]);

  expect(fetchMock).toHaveBeenCalledWith("/api/chunks/lookup", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chunk_ids: ["a"] }),
  });
  expect(result.chunks).toEqual([]);
});

test("getEvalRunLogs builds level query param", async () => {
  const payload = { tag: "t", window: null, entries: [], count: 0, truncated: false, holdout_redacted: false };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await getEvalRunLogs("t", { level: "info" });

  expect(fetchMock).toHaveBeenCalledWith("/api/evals/runs/t/logs?level=info");
  expect(result.count).toBe(0);
});
