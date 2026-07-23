import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getTrace, listTraces } from "@/api/client";
import TraceView from "@/components/TraceView";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Panel, PanelBody, PanelHeader, PanelTitle } from "@/components/ui/panel";
import { formatDate } from "@/lib/format";
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
    <div className="mx-auto max-w-[1240px] space-y-4">
      <PageHeader
        eyebrow="Persisted traces"
        title="Observability"
        subtitle="Browse persisted retrieval traces. Click a row to inspect its full trace."
      />

      <div className="mb-3.5 flex items-center gap-2.5">
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
        <span className="font-mono text-[12.5px] text-faint">
          {tracesQuery.data?.traces.length ?? 0} traces
        </span>
      </div>
      {tracesQuery.isLoading ? <p className="text-muted-foreground">Loading…</p> : null}
      {tracesQuery.error ? <p className="text-danger">Failed to load traces.</p> : null}

      <Panel>
        <PanelHeader>
          <PanelTitle>Trace History</PanelTitle>
        </PanelHeader>
        <PanelBody className="px-0 py-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted">
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
                  <TableCell className="whitespace-nowrap text-[12px] text-faint">
                    {formatDate(trace.timestamp, { withTime: true })}
                  </TableCell>
                  <TableCell>{truncate(trace.question)}</TableCell>
                  <TableCell className="font-mono text-[11px] text-muted-foreground">{trace.strategy ?? "n/a"}</TableCell>
                  <TableCell className="font-mono text-[11px] text-muted-foreground">{trace.latency_ms ?? "n/a"} ms</TableCell>
                  <TableCell className="whitespace-nowrap font-mono text-[11px] text-muted-foreground">
                    r {trace.stage_counts.retrieved ?? 0} / p{" "}
                    {trace.stage_counts.pre_expansion ?? 0} / s{" "}
                    {trace.stage_counts.selected ?? 0}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      {trace.abstained && <Badge variant="outline">abstained</Badge>}
                      {trace.error && <Badge variant="danger">error</Badge>}
                      {!trace.abstained && !trace.error && (
                        <span className="text-[11px] font-semibold text-primary">answered</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-[11px] text-faint">{trace.trace_label ?? ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </PanelBody>
      </Panel>

      {selectedId && traceQuery.isLoading && <p className="text-muted-foreground">Loading trace...</p>}
      {selectedId && traceQuery.error && <p className="text-danger">Trace not found.</p>}
      {traceQuery.data && <TraceView trace={traceQuery.data} />}
    </div>
  );
}
