import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listSyncRuns, startSync, type SyncRun } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import {
  Panel,
  PanelBody,
  PanelHeader,
  PanelTitle,
} from "@/components/ui/panel";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toneVariant } from "@/lib/status";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

function RunRow({ run }: { run: SyncRun }) {
  return (
    <TableRow>
      <TableCell className="text-[12px] text-muted-foreground">
        {formatDate(run.started_at, { withTime: true })}
      </TableCell>
      <TableCell className="text-[12px] text-muted-foreground">
        {formatDate(run.completed_at, { withTime: true })}
      </TableCell>
      <TableCell>
        <Badge variant={toneVariant(run.status)}>{run.status ?? "unknown"}</Badge>
      </TableCell>
      <TableCell className="text-right font-mono">{run.scanned_count ?? 0}</TableCell>
      <TableCell
        className={cn(
          "text-right font-mono",
          (run.changed_count ?? 0) > 0 && "text-gold",
        )}
      >
        {run.changed_count ?? 0}
      </TableCell>
      <TableCell className="text-right font-mono">{run.unchanged_count ?? 0}</TableCell>
      <TableCell
        className={cn(
          "text-right font-mono",
          (run.failed_count ?? 0) > 0 && "text-danger",
        )}
      >
        {run.failed_count ?? 0}
      </TableCell>
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
    <div className="mx-auto max-w-[1180px]">
      <PageHeader
        eyebrow="Corpus refresh"
        title="Ingestion"
        subtitle="Sync runs fetch, normalize, hash, chunk, embed and index all enabled sources."
        actions={
          <Button disabled={syncing} onClick={() => start.mutate()}>
            {syncing ? "Syncing…" : "Run sync"}
          </Button>
        }
      />

      {start.isError ? (
        <p className="mb-3 text-sm text-danger">Failed to start sync.</p>
      ) : null}
      {timeoutNotice ? (
        <p className="mb-3 text-sm text-muted-foreground">
          Sync is taking longer than expected — check back shortly.
        </p>
      ) : null}

      <Panel>
        <PanelHeader>
          <PanelTitle>History</PanelTitle>
        </PanelHeader>
        <PanelBody className="px-0 py-0">
          {runsQuery.isLoading ? (
            <p className="px-[18px] py-3 text-muted-foreground">Loading…</p>
          ) : null}
          {runsQuery.error ? (
            <p className="px-[18px] py-3 text-danger">Failed to load sync runs.</p>
          ) : null}
          {runsQuery.data ? (
            <Table>
              <TableHeader>
                <TableRow className="bg-muted">
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
          ) : null}
        </PanelBody>
      </Panel>
    </div>
  );
}
