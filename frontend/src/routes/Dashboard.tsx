import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getConfig, getHealth, getStats } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function statusVariant(status?: string | null): "default" | "secondary" | "destructive" | "outline" {
  if (status === "ok" || status === "completed") return "default";
  if (status === "failed" || status === "degraded") return "destructive";
  if (status === "partial") return "secondary";
  return "outline";
}

function Flag({ value }: { value: boolean }) {
  return <Badge variant={value ? "default" : "outline"}>{value ? "on" : "off"}</Badge>;
}

function StatCard({
  title,
  value,
  note,
}: {
  title: string;
  value: string | number;
  note?: string | undefined;
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-2xl">{value}</CardTitle>
      </CardHeader>
      {note && <CardContent className="text-sm text-muted-foreground">{note}</CardContent>}
    </Card>
  );
}

export default function Dashboard() {
  const statsQuery = useQuery({ queryKey: ["stats"], queryFn: getStats });
  const configQuery = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth });

  if (statsQuery.isLoading || configQuery.isLoading || healthQuery.isLoading) return <p>Loading…</p>;
  if (statsQuery.error || configQuery.error || healthQuery.error) {
    return <p className="text-red-600">Failed to load dashboard.</p>;
  }

  const stats = statsQuery.data!;
  const config = configQuery.data!;
  const health = healthQuery.data!;
  const lastSync = stats.last_sync;

  const modelRows = [
    ["Embedding backend", config.embedding_backend],
    ["Embedding model", config.embedding_model ?? "—"],
    ["Embedding dim", config.embedding_dim ?? "—"],
    ["LLM model", config.llm_model],
    ["Generator", config.generator_backend],
    ["Reranker", config.reranker_backend],
    ["Qdrant collection", config.qdrant_collection],
    ["Qdrant URL", config.qdrant_url],
    ["Ollama URL", config.ollama_base_url],
    ["AWS region", config.aws_region],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Corpus, index, and runtime status.</p>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          title="Documents"
          value={`${stats.documents_enabled}/${stats.documents_total}`}
          note="enabled / total"
        />
        <StatCard title="Chunks" value={stats.chunks_total} />
        <StatCard
          title="Qdrant points"
          value={stats.qdrant_points ?? "—"}
          note={stats.qdrant_points === null ? "Qdrant unavailable" : undefined}
        />
        <StatCard title="Conversations" value={stats.conversations_total} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardHeader>
            <CardTitle>By Category</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Category</TableHead>
                  <TableHead className="text-right">Documents</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {stats.by_category.map((row) => (
                  <TableRow key={row.category}>
                    <TableCell>{row.category}</TableCell>
                    <TableCell className="text-right">{row.count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Health</CardTitle>
            <CardDescription>
              <Badge variant={statusVariant(health.status)}>{health.status}</Badge>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between gap-3">
              <span>Qdrant</span>
              <Badge variant={health.qdrant ? "default" : "destructive"}>
                {health.qdrant ? "ok" : "down"}
              </Badge>
            </div>
            <div className="flex justify-between gap-3">
              <span>Ollama</span>
              <Badge variant={health.ollama === false ? "destructive" : "outline"}>
                {health.ollama === null ? "n/a" : health.ollama ? "ok" : "down"}
              </Badge>
            </div>
            <div className="flex justify-between gap-3">
              <span>Generator</span>
              <Badge variant="secondary">{health.generator_backend}</Badge>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardHeader>
            <CardTitle>Config Summary</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm md:grid-cols-2">
            {modelRows.map(([label, value]) => (
              <div key={label} className="grid gap-1">
                <span className="text-muted-foreground">{label}</span>
                <span className="break-all font-medium">{value}</span>
              </div>
            ))}
            <div className="grid gap-1">
              <span className="text-muted-foreground">Chunking</span>
              <span className="font-medium">
                {config.chunk_size} / {config.chunk_overlap}
              </span>
            </div>
            <div className="grid gap-1">
              <span className="text-muted-foreground">Minimum chunks</span>
              <span className="font-medium">{config.min_chunks_for_answer}</span>
            </div>
            <div className="grid gap-1">
              <span className="text-muted-foreground">Conversation turns</span>
              <span className="font-medium">{config.max_conversation_turns}</span>
            </div>
            <div className="flex flex-wrap gap-2 md:col-span-2">
              <span>Router <Flag value={config.router_enabled} /></span>
              <span>Edges <Flag value={config.edge_expansion_enabled} /></span>
              <span>Answerability <Flag value={config.answerability_gate_enabled} /></span>
              <span>Rewrite <Flag value={config.enable_query_rewriting} /></span>
              <span>Self-check <Flag value={config.faithfulness_selfcheck_enabled} /></span>
              <span>Later enacted <Flag value={config.later_enacted_preference_enabled} /></span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Last Sync</CardTitle>
            <CardDescription>
              <Link to="/ingestion" className="hover:underline">
                View sync history
              </Link>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {lastSync ? (
              <>
                <Badge variant={statusVariant(lastSync.status)}>{lastSync.status ?? "unknown"}</Badge>
                <div>Scanned: {lastSync.scanned_count ?? 0}</div>
                <div>Changed: {lastSync.changed_count ?? 0}</div>
                <div>Unchanged: {lastSync.unchanged_count ?? 0}</div>
                <div>Failed: {lastSync.failed_count ?? 0}</div>
                <div className="text-muted-foreground">{lastSync.completed_at ?? "running"}</div>
              </>
            ) : (
              <p className="text-muted-foreground">No sync runs yet.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
