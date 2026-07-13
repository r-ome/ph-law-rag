import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getEvalRunLogs } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type Level = "all" | "debug" | "info" | "warning" | "error";

function levelVariant(level?: string | null): "default" | "secondary" | "destructive" | "outline" {
  if (level === "error" || level === "critical") return "destructive";
  if (level === "warning") return "secondary";
  if (level === "info") return "default";
  return "outline";
}

export default function EvalRunLogs({ tag }: { tag: string }) {
  const [level, setLevel] = useState<Level>("all");
  const [loggerFilter, setLoggerFilter] = useState("");
  const logsQuery = useQuery({
    queryKey: ["evalRunLogs", tag, level],
    queryFn: () => getEvalRunLogs(tag, level === "all" ? undefined : { level }),
  });

  const data = logsQuery.data;
  const entries = (data?.entries ?? []).filter((entry) =>
    loggerFilter.trim() ? (entry.logger ?? "").toLowerCase().includes(loggerFilter.trim().toLowerCase()) : true,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Run Logs</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
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
        {logsQuery.error && <p className="text-sm text-red-600">Failed to load run logs.</p>}

        {data?.holdout_redacted ? (
          <p className="text-sm text-muted-foreground">Holdout run — logs are redacted.</p>
        ) : data && data.window == null ? (
          <p className="text-sm text-muted-foreground">No run window recorded (legacy run).</p>
        ) : data && data.entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No entries in window — the app log has likely rotated past this run.
          </p>
        ) : data ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Timestamp</TableHead>
                <TableHead>Level</TableHead>
                <TableHead>Logger</TableHead>
                <TableHead>Event</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry, index) => (
                <TableRow key={`${entry.timestamp ?? index}-${index}`} className="font-mono text-xs">
                  <TableCell className="whitespace-nowrap">{entry.timestamp ?? "n/a"}</TableCell>
                  <TableCell>
                    <Badge variant={levelVariant(entry.level)}>{entry.level ?? "raw"}</Badge>
                  </TableCell>
                  <TableCell>{entry.logger ?? ""}</TableCell>
                  <TableCell className="break-all">{entry.event ?? entry.raw ?? ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
    </Card>
  );
}
