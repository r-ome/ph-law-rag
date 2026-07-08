import { useQuery } from "@tanstack/react-query";
import { getConfig, getHealth } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function ServiceRow({
  label,
  value,
}: {
  label: string;
  value: boolean | null | string;
}) {
  const text = typeof value === "string" ? value : value === null ? "n/a" : value ? "ok" : "down";
  const variant = typeof value === "boolean" && !value ? "destructive" : "default";
  return (
    <div className="flex items-center justify-between gap-3 border-b py-3 last:border-0">
      <span className="font-medium">{label}</span>
      <Badge variant={variant}>{text}</Badge>
    </div>
  );
}

export default function Health() {
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const configQuery = useQuery({ queryKey: ["config"], queryFn: getConfig });

  if (healthQuery.isLoading) return <p>Loading…</p>;
  if (healthQuery.error) return <p className="text-red-600">Failed to load health.</p>;

  const health = healthQuery.data!;

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Health</h1>
          <p className="text-sm text-muted-foreground">Service status and generator backend.</p>
        </div>
        <Button variant="outline" onClick={() => healthQuery.refetch()}>
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Status
            <Badge variant={health.status === "ok" ? "default" : "destructive"}>
              {health.status}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ServiceRow label="Qdrant" value={health.qdrant} />
          <ServiceRow label="Ollama" value={health.ollama ?? null} />
          <ServiceRow label="Generator" value={health.generator_backend} />
          {configQuery.data && (
            <ServiceRow label="Embedding backend" value={configQuery.data.embedding_backend} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
