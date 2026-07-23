import { useQuery } from "@tanstack/react-query";
import { getConfig, getHealth } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import {
  Panel,
  PanelBody,
  PanelHeader,
  PanelTitle,
} from "@/components/ui/panel";
import { toneVariant } from "@/lib/status";

function StatusRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: ReturnType<typeof toneVariant>;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border py-3 last:border-0">
      <span className="text-[13.5px] font-medium">{label}</span>
      <Badge variant={tone} className="font-mono">
        {value}
      </Badge>
    </div>
  );
}

export default function Health() {
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const configQuery = useQuery({ queryKey: ["config"], queryFn: getConfig });

  if (healthQuery.isLoading)
    return <p className="text-muted-foreground">Loading…</p>;
  if (healthQuery.error)
    return <p className="text-danger">Failed to load health.</p>;

  const health = healthQuery.data!;

  return (
    <div className="mx-auto max-w-[760px]">
      <PageHeader
        eyebrow="Service status"
        title="Health"
        actions={
          <Button variant="outline" onClick={() => healthQuery.refetch()}>
            Refresh
          </Button>
        }
      />

      <Panel>
        <PanelHeader className="justify-start gap-2.5">
          <PanelTitle>Status</PanelTitle>
          <Badge variant={toneVariant(health.status)}>{health.status}</Badge>
        </PanelHeader>
        <PanelBody className="py-1.5">
          <StatusRow
            label="Qdrant"
            value={health.qdrant ? "ok" : "down"}
            tone={health.qdrant ? "tint-primary" : "danger"}
          />
          <StatusRow
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
          <StatusRow
            label="Generator"
            value={health.generator_backend}
            tone="secondary"
          />
          {configQuery.data ? (
            <StatusRow
              label="Embedding backend"
              value={configQuery.data.embedding_backend}
              tone="secondary"
            />
          ) : null}
        </PanelBody>
      </Panel>
    </div>
  );
}
