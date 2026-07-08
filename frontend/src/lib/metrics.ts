import { cn } from "@/lib/utils";

export function fmtMetric(x: number | null | undefined): string {
  return x == null ? "—" : x.toFixed(3);
}

export function deltaClass(d: number | null | undefined): string {
  if (d == null || d === 0) return "text-muted-foreground";
  return d > 0 ? "text-emerald-700" : "text-red-700";
}

export function fmtDelta(d: number | null | undefined): string {
  if (d == null) return "—";
  if (d === 0) return "0.000";
  return `${d > 0 ? "+" : "−"}${Math.abs(d).toFixed(3)}`;
}

export function metricDeltaClass(d: number | null | undefined, className?: string): string {
  return cn(deltaClass(d), className);
}
