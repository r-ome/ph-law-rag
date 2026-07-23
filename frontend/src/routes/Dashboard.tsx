import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getConfig, getHealth, getStats } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { StatTile } from "@/components/ui/stat-tile";
import { ProgressBar } from "@/components/ui/progress-bar";
import {
  Panel,
  PanelBody,
  PanelHeader,
  PanelMeta,
  PanelTitle,
} from "@/components/ui/panel";
import { toneVariant } from "@/lib/status";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

function FlagPill({ label, on }: { label: string; on: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[11.5px] font-medium",
        on
          ? "border-primary-bd bg-primary-bg text-primary"
          : "border-border bg-muted text-faint"
      )}
    >
      <span
        className={cn("h-1.5 w-1.5 rounded-full", on ? "bg-primary" : "bg-faint")}
      />
      {label}
    </span>
  );
}

function ServiceLine({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: ReturnType<typeof toneVariant>;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border py-2 last:border-0">
      <span className="text-[12.5px] text-muted-foreground">{label}</span>
      <Badge variant={tone} className="font-mono">
        {value}
      </Badge>
    </div>
  );
}

function SyncMetric({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | string;
  accent?: boolean;
}) {
  return (
    <div>
      <div className="text-[11px] tracking-[0.05em] text-faint uppercase">{label}</div>
      <div
        className={cn(
          "font-mono text-base font-semibold",
          accent && "text-gold"
        )}
      >
        {value}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const statsQuery = useQuery({ queryKey: ["stats"], queryFn: getStats });
  const configQuery = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth });

  if (statsQuery.isLoading || configQuery.isLoading || healthQuery.isLoading)
    return <p className="text-muted-foreground">Loading…</p>;
  if (statsQuery.error || configQuery.error || healthQuery.error) {
    return <p className="text-danger">Failed to load dashboard.</p>;
  }

  const stats = statsQuery.data!;
  const config = configQuery.data!;
  const health = healthQuery.data!;
  const lastSync = stats.last_sync;
  const maxCat = Math.max(1, ...stats.by_category.map((c) => c.count));

  const configRows: Array<[string, string | number | null | undefined]> = [
    ["Profile", config.profile],
    ["Embedding backend", config.embedding_backend],
    ["Embedding model", config.embedding_model],
    ["Embedding dim", config.embedding_dim],
    ["Generator model", config.llm_model],
    ["Generator backend", config.generator_backend],
    ["Reranker", config.reranker_backend],
    ["Evidence gate", config.evidence_gate],
    ["Qdrant collection", config.qdrant_collection],
    ["Chunking", `${config.chunk_size} / ${config.chunk_overlap}`],
    ["Min chunks", config.min_chunks_for_answer],
    ["Conversation turns", config.max_conversation_turns],
  ];

  const flags: Array<[string, boolean]> = [
    ["CRAG", config.evidence_gate === "crag"],
    ["Corrective", config.corrective_retrieval_enabled],
    ["Router", config.router_enabled],
    ["Edges", config.edge_expansion_enabled],
    ["Answerability", config.answerability_gate_enabled],
    ["Rewrite", config.enable_query_rewriting],
    ["Self-check", config.faithfulness_selfcheck_enabled],
    ["Later enacted", config.later_enacted_preference_enabled],
  ];

  return (
    <div className="mx-auto max-w-[1240px]">
      <PageHeader
        eyebrow="Overview"
        title="Corpus & Runtime"
        subtitle="The state of the index, the retrieval pipeline, and the last corpus refresh."
        meta={
          lastSync?.completed_at ? (
            <>
              <div className="font-mono">{formatDate(lastSync.completed_at, { withTime: true })}</div>
              <div>last sync</div>
            </>
          ) : null
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3.5 xl:grid-cols-4">
        <StatTile
          label="Documents"
          value={`${stats.documents_enabled}/${stats.documents_total}`}
          note="enabled / total"
          tag="corpus"
          tagTint="primary"
        />
        <StatTile
          label="Chunks"
          value={stats.chunks_total.toLocaleString()}
          note="indexed passages"
          tag="index"
          tagTint="muted"
        />
        <StatTile
          label="Qdrant points"
          value={stats.qdrant_points == null ? "—" : stats.qdrant_points.toLocaleString()}
          note={stats.qdrant_points == null ? "Qdrant unavailable" : "vector store"}
          tag="vectors"
          tagTint="muted"
        />
        <StatTile
          label="Conversations"
          value={stats.conversations_total}
          note="recorded sessions"
          tag="chat"
          tagTint="muted"
        />
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Panel>
          <PanelHeader>
            <PanelTitle>Corpus by category</PanelTitle>
            <PanelMeta>{stats.documents_enabled} enabled</PanelMeta>
          </PanelHeader>
          <PanelBody className="flex flex-col gap-3.5">
            {stats.by_category.map((c) => (
              <div key={c.category}>
                <div className="mb-1.5 flex items-baseline justify-between">
                  <span className="text-[13px] font-medium">{c.category}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {c.count}
                  </span>
                </div>
                <ProgressBar value={(c.count / maxCat) * 100} />
              </div>
            ))}
          </PanelBody>
        </Panel>

        <div className="flex flex-col gap-4">
          <Panel>
            <PanelHeader>
              <PanelTitle className="text-[15.5px]">Health</PanelTitle>
              <Badge variant={toneVariant(health.status)}>{health.status}</Badge>
            </PanelHeader>
            <PanelBody className="py-1.5">
              <ServiceLine
                label="Qdrant"
                value={health.qdrant ? "ok" : "down"}
                tone={health.qdrant ? "tint-primary" : "danger"}
              />
              <ServiceLine
                label="Ollama"
                value={health.ollama == null ? "n/a" : health.ollama ? "ok" : "down"}
                tone={
                  health.ollama == null
                    ? "secondary"
                    : health.ollama
                      ? "tint-primary"
                      : "danger"
                }
              />
              <ServiceLine
                label="Generator"
                value={health.generator_backend}
                tone="secondary"
              />
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHeader>
              <PanelTitle className="text-[15.5px]">Last sync</PanelTitle>
              {lastSync ? (
                <Badge variant={toneVariant(lastSync.status)}>
                  {lastSync.status ?? "unknown"}
                </Badge>
              ) : null}
            </PanelHeader>
            <PanelBody>
              {lastSync ? (
                <div className="grid grid-cols-2 gap-x-3.5 gap-y-2.5">
                  <SyncMetric label="Scanned" value={lastSync.scanned_count ?? 0} />
                  <SyncMetric
                    label="Changed"
                    value={lastSync.changed_count ?? 0}
                    accent={(lastSync.changed_count ?? 0) > 0}
                  />
                  <SyncMetric label="Unchanged" value={lastSync.unchanged_count ?? 0} />
                  <SyncMetric label="Failed" value={lastSync.failed_count ?? 0} />
                  <div className="col-span-2 font-mono text-[11.5px] text-faint">
                    <Link to="/ingestion" className="hover:underline">
                      {formatDate(lastSync.completed_at ?? "running", { withTime: true })}
                    </Link>
                  </div>
                </div>
              ) : (
                <p className="text-muted-foreground">No sync runs yet.</p>
              )}
            </PanelBody>
          </Panel>
        </div>
      </div>

      <Panel>
        <PanelHeader>
          <PanelTitle>Pipeline configuration</PanelTitle>
        </PanelHeader>
        <PanelBody className="grid grid-cols-2 gap-x-[18px] gap-y-3.5 md:grid-cols-4">
          {configRows.map(([label, value]) => (
            <div key={label} className="flex min-w-0 flex-col gap-0.5">
              <span className="text-[11px] tracking-[0.05em] text-faint uppercase">
                {label}
              </span>
              <span className="truncate font-mono text-[12.5px] font-medium">
                {value ?? "—"}
              </span>
            </div>
          ))}
        </PanelBody>
        <div className="flex flex-wrap gap-2 border-t border-border px-[18px] py-3.5">
          {flags.map(([label, on]) => (
            <FlagPill key={label} label={label} on={on} />
          ))}
        </div>
      </Panel>
    </div>
  );
}
