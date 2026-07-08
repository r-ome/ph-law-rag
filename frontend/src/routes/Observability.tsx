import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getTrace, listTraces } from "@/api/client";
import TraceView from "@/components/TraceView";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function truncate(text: string, length = 76): string {
  return text.length > length ? `${text.slice(0, length - 1)}...` : text;
}

export default function Observability() {
  const [date, setDate] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const tracesQuery = useQuery({
    queryKey: ["traces", date],
    queryFn: () => listTraces(date ? { limit: 100, date } : { limit: 100 }),
  });

  const traceQuery = useQuery({
    queryKey: ["trace", selectedId],
    queryFn: () => getTrace(selectedId!),
    enabled: Boolean(selectedId),
    retry: false,
  });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Observability</h1>
        <p className="text-sm text-muted-foreground">Browse persisted retrieval traces.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Trace History</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input
            value={date}
            type="date"
            aria-label="Trace date"
            className="w-[180px]"
            onChange={(e) => {
              setDate(e.target.value);
              setSelectedId(null);
            }}
          />
          {tracesQuery.isLoading && <p>Loading...</p>}
          {tracesQuery.error && <p className="text-sm text-red-600">Failed to load traces.</p>}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Timestamp</TableHead>
                <TableHead>Question</TableHead>
                <TableHead>Strategy</TableHead>
                <TableHead>Latency</TableHead>
                <TableHead>Counts</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Label</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(tracesQuery.data?.traces ?? []).map((trace) => (
                <TableRow
                  key={trace.trace_id}
                  className="cursor-pointer"
                  onClick={() => setSelectedId(trace.trace_id)}
                >
                  <TableCell className="whitespace-nowrap text-xs">{trace.timestamp ?? "n/a"}</TableCell>
                  <TableCell>{truncate(trace.question)}</TableCell>
                  <TableCell>{trace.strategy ?? "n/a"}</TableCell>
                  <TableCell>{trace.latency_ms ?? "n/a"} ms</TableCell>
                  <TableCell className="whitespace-nowrap text-xs">
                    r {trace.stage_counts.retrieved ?? 0} / p{" "}
                    {trace.stage_counts.pre_expansion ?? 0} / s{" "}
                    {trace.stage_counts.selected ?? 0}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      {trace.abstained && <Badge variant="outline">abstained</Badge>}
                      {trace.error && <Badge variant="destructive">error</Badge>}
                    </div>
                  </TableCell>
                  <TableCell>{trace.trace_label ?? ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {selectedId && traceQuery.isLoading && <p>Loading trace...</p>}
      {selectedId && traceQuery.error && <p className="text-sm text-red-600">Trace not found.</p>}
      {traceQuery.data && <TraceView trace={traceQuery.data} />}
    </div>
  );
}
