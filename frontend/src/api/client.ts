import type { paths } from "@/api/schema";

export type DocumentListResponse =
  paths["/documents"]["get"]["responses"]["200"]["content"]["application/json"];
export type DocumentDetail =
  paths["/documents/{doc_id}"]["get"]["responses"]["200"]["content"]["application/json"];
export type ChunkListResponse =
  paths["/documents/{doc_id}/chunks"]["get"]["responses"]["200"]["content"]["application/json"];
type AskBody =
  paths["/query/ask"]["post"]["requestBody"]["content"]["application/json"];
type AskResponse =
  paths["/query/ask"]["post"]["responses"]["200"]["content"]["application/json"];
type ConversationListResponse =
  paths["/conversations"]["get"]["responses"]["200"]["content"]["application/json"];
type ConversationDetail =
  paths["/conversations/{session_id}"]["get"]["responses"]["200"]["content"]["application/json"];
type StatsOverview =
  paths["/stats/overview"]["get"]["responses"]["200"]["content"]["application/json"];
type ConfigView =
  paths["/config"]["get"]["responses"]["200"]["content"]["application/json"];
type HealthStatus =
  paths["/health"]["get"]["responses"]["200"]["content"]["application/json"];
type SyncRunListResponse =
  paths["/sync/runs"]["get"]["responses"]["200"]["content"]["application/json"];
type SyncStartedResponse =
  paths["/documents/sync"]["post"]["responses"]["200"]["content"]["application/json"];
type InspectBody =
  paths["/retrieval/inspect"]["post"]["requestBody"]["content"]["application/json"];
type InspectResponse =
  paths["/retrieval/inspect"]["post"]["responses"]["200"]["content"]["application/json"];
type TraceListResponse =
  paths["/traces"]["get"]["responses"]["200"]["content"]["application/json"];
type TraceRecord =
  paths["/traces/{trace_id}"]["get"]["responses"]["200"]["content"]["application/json"];
type LogResponse =
  paths["/logs"]["get"]["responses"]["200"]["content"]["application/json"];
type EvalRunListResponse =
  paths["/evals/runs"]["get"]["responses"]["200"]["content"]["application/json"];
type EvalRunDetail =
  paths["/evals/runs/{tag}"]["get"]["responses"]["200"]["content"]["application/json"];
type EvalRowsResponse =
  paths["/evals/runs/{tag}/rows"]["get"]["responses"]["200"]["content"]["application/json"];
type EvalDiff =
  paths["/evals/runs/{tag}/diff"]["get"]["responses"]["200"]["content"]["application/json"];

export type DocumentSummary = DocumentListResponse["documents"][number];
export type ChatSource = AskResponse["sources"][number];
export type ConversationSummary = ConversationListResponse["conversations"][number];
export type ConversationTurn = ConversationDetail["turns"][number];
export type SyncRun = SyncRunListResponse["runs"][number];
export type TraceSummary = TraceListResponse["traces"][number];
export type { TraceRecord };
export type ChunkTrace = TraceRecord["retrieved_chunks"][number];
export type EvalRunSummary = EvalRunListResponse["runs"][number];
export type EvalRow = EvalRowsResponse["rows"][number];
export type { EvalRunDetail, EvalDiff };

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return (await res.json()) as T;
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return (await res.json()) as T;
}

export function listDocuments(): Promise<DocumentListResponse> {
  return apiGet<DocumentListResponse>("/documents");
}

export function getDocument(docId: string): Promise<DocumentDetail> {
  return apiGet<DocumentDetail>(`/documents/${encodeURIComponent(docId)}`);
}

export function listChunks(docId: string): Promise<ChunkListResponse> {
  return apiGet<ChunkListResponse>(`/documents/${encodeURIComponent(docId)}/chunks`);
}

export function ask(body: AskBody): Promise<AskResponse> {
  return apiPost<AskResponse>("/query/ask", body);
}

export function listConversations(): Promise<ConversationListResponse> {
  return apiGet<ConversationListResponse>("/conversations");
}

export function getConversation(sessionId: string): Promise<ConversationDetail> {
  return apiGet<ConversationDetail>(`/conversations/${encodeURIComponent(sessionId)}`);
}

export function getStats(): Promise<StatsOverview> {
  return apiGet<StatsOverview>("/stats/overview");
}

export function getConfig(): Promise<ConfigView> {
  return apiGet<ConfigView>("/config");
}

export function getHealth(): Promise<HealthStatus> {
  return apiGet<HealthStatus>("/health");
}

export function listSyncRuns(): Promise<SyncRunListResponse> {
  return apiGet<SyncRunListResponse>("/sync/runs");
}

export function startSync(): Promise<SyncStartedResponse> {
  return apiPost<SyncStartedResponse>("/documents/sync", {});
}

export function inspectRetrieval(body: InspectBody): Promise<InspectResponse> {
  return apiPost<InspectResponse>("/retrieval/inspect", body);
}

export function listTraces(params?: { limit?: number; date?: string }): Promise<TraceListResponse> {
  const q = new URLSearchParams();
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.date) q.set("date", params.date);
  const qs = q.toString();
  return apiGet<TraceListResponse>(`/traces${qs ? `?${qs}` : ""}`);
}

export function getTrace(traceId: string): Promise<TraceRecord> {
  return apiGet<TraceRecord>(`/traces/${encodeURIComponent(traceId)}`);
}

export function getLogs(params?: { lines?: number; level?: string }): Promise<LogResponse> {
  const q = new URLSearchParams();
  if (params?.lines != null) q.set("lines", String(params.lines));
  if (params?.level) q.set("level", params.level);
  const qs = q.toString();
  return apiGet<LogResponse>(`/logs${qs ? `?${qs}` : ""}`);
}

export function listEvalRuns(): Promise<EvalRunListResponse> {
  return apiGet<EvalRunListResponse>("/evals/runs");
}

export function getEvalRun(tag: string): Promise<EvalRunDetail> {
  return apiGet<EvalRunDetail>(`/evals/runs/${encodeURIComponent(tag)}`);
}

export function getEvalRows(tag: string): Promise<EvalRowsResponse> {
  return apiGet<EvalRowsResponse>(`/evals/runs/${encodeURIComponent(tag)}/rows`);
}

export function getEvalDiff(tag: string, baseline: string): Promise<EvalDiff> {
  return apiGet<EvalDiff>(
    `/evals/runs/${encodeURIComponent(tag)}/diff?baseline=${encodeURIComponent(baseline)}`,
  );
}
