import type { LogEntry } from "@/api/client";
import { formatTime } from "@/lib/format";

function levelColor(level?: string | null): string {
  switch (level) {
    case "error":
    case "critical":
      return "var(--danger)";
    case "warning":
      return "var(--warn)";
    case "info":
      return "var(--primary)";
    case "debug":
      return "var(--faint)";
    default:
      return "var(--muted)";
  }
}

export function LogTable({ entries }: { entries: LogEntry[] }) {
  return (
    <div className="flex flex-col gap-0.5">
      {entries.map((entry, index) => (
        <div
          key={`${entry.timestamp ?? index}-${index}`}
          className="flex gap-2 font-mono text-[11.5px] leading-[1.6]"
        >
          <span className="shrink-0 text-faint">{formatTime(entry.timestamp) || "n/a"}</span>
          <span
            className="w-[52px] shrink-0 font-semibold uppercase"
            style={{ color: levelColor(entry.level) }}
          >
            {entry.level ?? "raw"}
          </span>
          <span className="break-all">
            {entry.event ?? entry.raw ?? ""}
            {entry.logger ? <span className="text-faint"> · {entry.logger}</span> : null}
          </span>
        </div>
      ))}
    </div>
  );
}
