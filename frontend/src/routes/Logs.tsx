import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getLogs } from "@/api/client";
import { LogTable } from "@/components/LogTable";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Logs</h1>
        <p className="text-sm text-muted-foreground">Tail of the structured app log.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>App Log</CardTitle>
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
            <Button variant="outline" onClick={() => logsQuery.refetch()}>
              Refresh
            </Button>
            <span className="text-sm text-muted-foreground">count {logsQuery.data?.count ?? 0}</span>
          </div>

          {logsQuery.isLoading && <p>Loading...</p>}
          {logsQuery.error && <p className="text-sm text-red-600">Failed to load logs.</p>}
          <LogTable entries={logsQuery.data?.entries ?? []} />
        </CardContent>
      </Card>
    </div>
  );
}
