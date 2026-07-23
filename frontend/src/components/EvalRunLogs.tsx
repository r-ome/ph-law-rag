import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getEvalRunLogs } from "@/api/client";
import { LogTable } from "@/components/LogTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Level = "all" | "debug" | "info" | "warning" | "error";

export default function EvalRunLogs({ tag }: { tag: string }) {
  const [level, setLevel] = useState<Level>("all");
  const [loggerFilter, setLoggerFilter] = useState("");
  const [expanded, setExpanded] = useState(false);
  const logsQuery = useQuery({
    queryKey: ["evalRunLogs", tag, level],
    queryFn: () => getEvalRunLogs(tag, level === "all" ? undefined : { level }),
    enabled: expanded,
  });

  const data = logsQuery.data;
  const loggerNeedle = loggerFilter.trim().toLowerCase();
  const entries = (data?.entries ?? []).filter((entry) =>
    loggerNeedle ? (entry.logger ?? "").toLowerCase().includes(loggerNeedle) : true,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Run Logs</CardTitle>
        <CardAction>
          <Button type="button" variant="outline" size="sm" onClick={() => setExpanded((value) => !value)}>
            {expanded ? "Hide logs" : "Show logs"}
          </Button>
        </CardAction>
      </CardHeader>
      {expanded && <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={level} onValueChange={(v) => setLevel((v ?? "all") as Level)}>
            <SelectTrigger className="w-[150px]" aria-label="Log level">
              <SelectValue placeholder="Level" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">all</SelectItem>
              <SelectItem value="debug">debug</SelectItem>
              <SelectItem value="info">info</SelectItem>
              <SelectItem value="warning">warning</SelectItem>
              <SelectItem value="error">error</SelectItem>
            </SelectContent>
          </Select>
          <Input
            placeholder="Filter by logger…"
            value={loggerFilter}
            onChange={(e) => setLoggerFilter(e.target.value)}
            className="w-[220px]"
          />
          {data?.truncated && <Badge variant="destructive">truncated at {data.count}</Badge>}
        </div>

        {data?.window ? (
          <p className="text-sm text-muted-foreground">
            Window: {data.window.started_at ?? "—"} → {data.window.completed_at ?? "—"}
          </p>
        ) : null}

        {logsQuery.isLoading && <p>Loading logs…</p>}
        {logsQuery.error && <p className="text-sm text-danger">Failed to load run logs.</p>}

        {data?.holdout_redacted ? (
          <p className="text-sm text-muted-foreground">Holdout run — logs are redacted.</p>
        ) : data && data.window == null ? (
          <p className="text-sm text-muted-foreground">No run window recorded (legacy run).</p>
        ) : data && data.entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No entries in window — the app log has likely rotated past this run.
          </p>
        ) : data && entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No entries match the logger filter.</p>
        ) : data ? (
          <LogTable entries={entries} />
        ) : null}
      </CardContent>}
    </Card>
  );
}
