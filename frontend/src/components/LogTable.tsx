import type { LogEntry } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function logLevelVariant(level?: string | null): "default" | "secondary" | "destructive" | "outline" {
  if (level === "error" || level === "critical") return "destructive";
  if (level === "warning") return "secondary";
  if (level === "info") return "default";
  return "outline";
}

export function LogTable({ entries }: { entries: LogEntry[] }) {
  return (
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
              <Badge variant={logLevelVariant(entry.level)}>{entry.level ?? "raw"}</Badge>
            </TableCell>
            <TableCell>{entry.logger ?? ""}</TableCell>
            <TableCell className="break-all">{entry.event ?? entry.raw ?? ""}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
