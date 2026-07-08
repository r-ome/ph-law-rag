import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listSyncRuns, startSync, type SyncRun } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function statusVariant(status?: string | null): "default" | "secondary" | "destructive" | "outline" {
  if (status === "completed") return "default";
  if (status === "partial") return "secondary";
  if (status === "failed") return "destructive";
  return "outline";
}

function RunRow({ run }: { run: SyncRun }) {
  return (
    <TableRow>
      <TableCell>{run.started_at ?? "—"}</TableCell>
      <TableCell>{run.completed_at ?? "—"}</TableCell>
      <TableCell>
        <Badge variant={statusVariant(run.status)}>{run.status ?? "unknown"}</Badge>
      </TableCell>
      <TableCell className="text-right">{run.scanned_count ?? 0}</TableCell>
      <TableCell className="text-right">{run.changed_count ?? 0}</TableCell>
      <TableCell className="text-right">{run.unchanged_count ?? 0}</TableCell>
      <TableCell className="text-right">{run.failed_count ?? 0}</TableCell>
    </TableRow>
  );
}

export default function Ingestion() {
  const queryClient = useQueryClient();
  const [watchId, setWatchId] = useState<string | null>(null);
  const [timeoutNotice, setTimeoutNotice] = useState(false);

  const start = useMutation({
    mutationFn: startSync,
    onSuccess: (res) => {
      setTimeoutNotice(false);
      setWatchId(res.sync_run_id);
      queryClient.invalidateQueries({ queryKey: ["syncRuns"] });
    },
  });

  const runsQuery = useQuery({
    queryKey: ["syncRuns"],
    queryFn: listSyncRuns,
    refetchInterval: () => (watchId ? 2500 : false),
  });

  const watched = runsQuery.data?.runs.find((r) => r.sync_run_id === watchId);

  useEffect(() => {
    if (watched && watched.status !== "running") setWatchId(null);
  }, [watched]);

  useEffect(() => {
    if (!watchId) return;
    const timer = window.setTimeout(() => {
      setWatchId(null);
      setTimeoutNotice(true);
    }, 120_000);
    return () => window.clearTimeout(timer);
  }, [watchId]);

  const syncing = start.isPending || Boolean(watchId);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Ingestion</h1>
          <p className="text-sm text-muted-foreground">Sync runs and corpus refresh status.</p>
        </div>
        <Button disabled={syncing} onClick={() => start.mutate()}>
          {syncing ? "Syncing…" : "Run sync"}
        </Button>
      </div>

      {start.isError && <p className="text-sm text-red-600">Failed to start sync.</p>}
      {timeoutNotice && (
        <p className="text-sm text-muted-foreground">
          Sync is taking longer than expected — check back shortly.
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>History</CardTitle>
        </CardHeader>
        <CardContent>
          {runsQuery.isLoading && <p>Loading…</p>}
          {runsQuery.error && <p className="text-red-600">Failed to load sync runs.</p>}
          {runsQuery.data && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Started</TableHead>
                  <TableHead>Completed</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Scanned</TableHead>
                  <TableHead className="text-right">Changed</TableHead>
                  <TableHead className="text-right">Unchanged</TableHead>
                  <TableHead className="text-right">Failed</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runsQuery.data.runs.map((run) => (
                  <RunRow key={run.sync_run_id} run={run} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
