import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getLogs, inspectRetrieval } from "@/api/client";
import type { LogEntry } from "@/api/client";
import { formatTime } from "@/lib/format";
import TraceView from "@/components/TraceView";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

type Strategy = "auto" | "default" | "current_law" | "sibling_aware";

const EVIDENCE_JUDGE_MODELS = [
  "gemma4:e4b",
  "gemma3:4b",
  "qwen3:4b",
  "mistral",
  "claude-haiku-4-5",
];

const PLANNER_MODELS = ["mistral", "gemma4:e4b", "gemma3:4b", "qwen3:4b", "claude-haiku-4-5"];

type Reranker = "default" | "minilm" | "qwen3" | "bedrock";

const RERANKERS: { value: Reranker; label: string }[] = [
  { value: "default", label: "default (env)" },
  { value: "minilm", label: "minilm (local)" },
  { value: "qwen3", label: "qwen3 (local)" },
  { value: "bedrock", label: "bedrock (amazon.rerank-v1)" },
];

function logLevelColor(level?: string | null): string {
  if (level === "error" || level === "critical") return "var(--danger)";
  if (level === "warning") return "var(--warn)";
  return "var(--muted)";
}

function splitExtra(extra: LogEntry["extra"]): { inline: string; blocks: [string, string][] } {
  if (!extra) return { inline: "", blocks: [] };
  const inlineParts: string[] = [];
  const blocks: [string, string][] = [];
  for (const [key, value] of Object.entries(extra)) {
    if (value == null || key === "trace_id") continue;
    const text = typeof value === "string" ? value : JSON.stringify(value);
    if (text.includes("\n") || text.length > 80) blocks.push([key, text]);
    else inlineParts.push(`${key}=${text}`);
  }
  return { inline: inlineParts.join(" "), blocks };
}

function LiveLogPanel({
  entries,
  running,
  started,
}: {
  entries: LogEntry[];
  running: boolean;
  started: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  return (
    <Card className="lg:sticky lg:top-4">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>Ask logs</CardTitle>
        {running && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
            live
          </span>
        )}
      </CardHeader>
      <CardContent>
        {!started && (
          <p className="text-sm text-muted-foreground">Run a question to see its logs live.</p>
        )}
        {started && entries.length === 0 && (
          <p className="text-sm text-muted-foreground">Waiting for log entries…</p>
        )}
        {entries.length > 0 && (
          <div ref={scrollRef} className="max-h-[70vh] space-y-1.5 overflow-y-auto pr-1">
            {entries.map((entry, index) => {
              const { inline, blocks } = splitExtra(entry.extra);
              return (
                <div
                  key={`${entry.timestamp ?? "raw"}-${index}`}
                  className="font-mono text-[11px] leading-5"
                >
                  <span className="text-muted-foreground">{formatTime(entry.timestamp)}</span>{" "}
                  <span className="font-semibold" style={{ color: logLevelColor(entry.level) }}>
                    {entry.level ?? "raw"}
                  </span>{" "}
                  <span className="break-all">{entry.event ?? entry.raw ?? ""}</span>
                  {inline && <span className="break-all text-muted-foreground"> {inline}</span>}
                  {blocks.map(([key, text]) => (
                    <details key={key} className="ml-4 mt-0.5">
                      <summary className="cursor-pointer select-none text-muted-foreground">
                        {key}
                      </summary>
                      <pre className="mt-1 max-h-60 overflow-y-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-2">
                        {text}
                      </pre>
                    </details>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function Lab() {
  const [question, setQuestion] = useState("");
  const [strategy, setStrategy] = useState<Strategy>("auto");
  const [cragEnabled, setCragEnabled] = useState(false);
  const [cragJudgeModel, setCragJudgeModel] = useState(EVIDENCE_JUDGE_MODELS[0]!);
  const [decompositionEnabled, setDecompositionEnabled] = useState(false);
  const [plannerModel, setPlannerModel] = useState(PLANNER_MODELS[0]!);
  const [reranker, setReranker] = useState<Reranker>("default");
  const [runStartedAt, setRunStartedAt] = useState<Date | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      inspectRetrieval({
        question: question.trim(),
        strategy: strategy === "auto" ? null : strategy,
        overrides:
          cragEnabled || decompositionEnabled || reranker !== "default"
            ? {
                ...(cragEnabled
                  ? {
                      evidence_gate: "crag" as const,
                      evidence_judge_model: cragJudgeModel,
                      corrective_retrieval_enabled: true,
                    }
                  : {}),
                ...(decompositionEnabled
                  ? { query_decomposition_enabled: true, query_planner_model: plannerModel }
                  : {}),
                ...(reranker !== "default" ? { reranker_backend: reranker } : {}),
              }
            : null,
      }),
    onSettled: () => {
      // one catch-up poll so the tail of the run isn't lost when polling stops
      void logsQuery.refetch();
    },
  });

  const logsQuery = useQuery({
    queryKey: ["labAskLogs"],
    queryFn: () => getLogs({ lines: 500 }),
    enabled: runStartedAt != null,
    refetchInterval: mutation.isPending ? 1000 : false,
  });

  const sessionEntries = (logsQuery.data?.entries ?? []).filter((entry) => {
    if (!runStartedAt || !entry.timestamp) return false;
    const ts = new Date(entry.timestamp).getTime();
    return !Number.isNaN(ts) && ts >= runStartedAt.getTime();
  });

  function run() {
    if (!question.trim() || mutation.isPending) return;
    setRunStartedAt(new Date());
    mutation.mutate();
  }

  const data = mutation.data;

  return (
    <div className="mx-auto max-w-[1300px] gap-5 space-y-5 lg:grid lg:grid-cols-[minmax(0,1fr)_400px] lg:space-y-0">
      <div className="space-y-5">
        <PageHeader
          eyebrow="Trace inspector"
          title="Retrieval Lab"
          subtitle="Run one query and inspect the full retrieval trace, stage by stage."
        />

        <Card>
          <CardContent className="space-y-3">
            <Textarea
              value={question}
              disabled={mutation.isPending}
              placeholder="Ask a legal question..."
              className="min-h-28 resize-none"
              onChange={(e) => setQuestion(e.target.value)}
            />
            <div className="space-y-2 rounded-lg border border-border p-3">
              <span className="text-sm font-medium">Config</span>
              <div className="flex flex-wrap items-center gap-4">
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={cragEnabled}
                    disabled={mutation.isPending}
                    onChange={(e) => setCragEnabled(e.target.checked)}
                    className="h-4 w-4 accent-primary"
                  />
                  CRAG evidence gate
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={decompositionEnabled}
                    disabled={mutation.isPending}
                    onChange={(e) => setDecompositionEnabled(e.target.checked)}
                    className="h-4 w-4 accent-primary"
                  />
                  Query decomposition
                </label>
                {decompositionEnabled && (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">Planner model</span>
                    <Select value={plannerModel} onValueChange={(v) => v && setPlannerModel(v)}>
                      <SelectTrigger className="w-[200px]" aria-label="Query planner model">
                        <SelectValue placeholder="Planner model" />
                      </SelectTrigger>
                      <SelectContent>
                        {PLANNER_MODELS.map((model) => (
                          <SelectItem key={model} value={model}>
                            {model}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Reranker</span>
                  <Select
                    value={reranker}
                    onValueChange={(v) => v && setReranker(v as Reranker)}
                  >
                    <SelectTrigger className="w-[240px]" aria-label="Reranker backend">
                      <SelectValue placeholder="Reranker" />
                    </SelectTrigger>
                    <SelectContent>
                      {RERANKERS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {cragEnabled && (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">Evidence judge</span>
                    <Select value={cragJudgeModel} onValueChange={(v) => v && setCragJudgeModel(v)}>
                      <SelectTrigger className="w-[200px]" aria-label="Evidence judge model">
                        <SelectValue placeholder="Evidence judge" />
                      </SelectTrigger>
                      <SelectContent>
                        {EVIDENCE_JUDGE_MODELS.map((model) => (
                          <SelectItem key={model} value={model}>
                            {model}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={strategy} onValueChange={(v) => setStrategy((v ?? "auto") as Strategy)}>
                <SelectTrigger className="w-[180px]" aria-label="Strategy">
                  <SelectValue placeholder="Strategy" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Auto (router)</SelectItem>
                  <SelectItem value="default">default</SelectItem>
                  <SelectItem value="current_law">current_law</SelectItem>
                  <SelectItem value="sibling_aware">sibling_aware (experimental)</SelectItem>
                </SelectContent>
              </Select>
              <Button disabled={mutation.isPending || !question.trim()} onClick={run}>
                {mutation.isPending ? "Running..." : "Run"}
              </Button>
            </div>
          </CardContent>
        </Card>

        {mutation.isError && <p className="text-sm text-danger">Failed to run retrieval.</p>}
        {data?.error && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-danger">
            {data.error_message ?? "Retrieval returned an error."}
          </p>
        )}
        {data && !data.error && (
          <Card>
            <CardHeader>
              <CardTitle>Answer</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="font-serif text-sm leading-[1.6] whitespace-pre-wrap">{data.answer}</div>
              {data.sources.length > 0 && (
                <div className="grid gap-2 md:grid-cols-2">
                  {data.sources.map((source) => (
                    <div key={source.ref} className="rounded-lg border border-border bg-muted p-3 text-sm">
                      <div className="font-medium">
                        [{source.ref}] {source.title}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-2">
                        <Badge variant="secondary">{source.source_id}</Badge>
                        {source.locator && <Badge variant="outline">{source.locator}</Badge>}
                      </div>
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-2 block break-all text-xs hover:underline"
                      >
                        {source.url}
                      </a>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        )}
        {data?.trace && <TraceView trace={data.trace} />}
        {data && !data.trace && (
          <p className="text-sm text-muted-foreground">No trace was returned for this run.</p>
        )}
      </div>

      <LiveLogPanel
        entries={sessionEntries}
        running={mutation.isPending}
        started={runStartedAt != null}
      />
    </div>
  );
}
