import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getLogs } from "@/api/client";
import { LogTable } from "@/components/LogTable";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Level = "all" | "debug" | "info" | "warning" | "error";

export default function Logs() {
  const [level, setLevel] = useState<Level>("all");
  const lines = 200;
  const logsQuery = useQuery({
    queryKey: ["logs", level, lines],
    queryFn: () => getLogs(level === "all" ? { lines } : { lines, level }),
  });

  return (
    <div className="mx-auto max-w-[1180px]">
      <PageHeader
        eyebrow="Structured log"
        title="Logs"
        subtitle="Tail of the structured application log."
      />

      <div className="mb-3.5 flex flex-wrap items-center gap-2.5">
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
        <Button variant="outline" onClick={() => logsQuery.refetch()}>
          Refresh
        </Button>
        <span className="font-mono text-[12.5px] text-faint">
          count {logsQuery.data?.count ?? 0}
        </span>
      </div>

      {logsQuery.isLoading ? <p className="text-muted-foreground">Loading…</p> : null}
      {logsQuery.error ? <p className="text-danger">Failed to load logs.</p> : null}

      <ScrollArea className="h-[calc(100vh-220px)] max-h-[calc(100vh-220px)] overflow-hidden rounded-xl border border-border bg-card shadow-[var(--shadow)]">
        <div className="px-4 py-3.5">
          <LogTable entries={logsQuery.data?.entries ?? []} />
        </div>
      </ScrollArea>
    </div>
  );
}
