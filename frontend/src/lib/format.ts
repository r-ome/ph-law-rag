export function formatDate(
  value?: string | number | null,
  opts?: { withTime?: boolean },
): string {
  if (value == null || value === "") return "—";
  const raw = String(value).trim();
  // Leave bare years and anything non-date-ish as-is.
  if (/^\d{4}$/.test(raw)) return raw;
  // Parse date-only strings as local midnight (avoid UTC off-by-one).
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T00:00:00` : raw;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return raw;
  const base: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "long",
    day: "numeric",
  };
  return opts?.withTime
    ? d.toLocaleString(undefined, { ...base, hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, base);
}

// Time-of-day only (24h with seconds) — for high-frequency log lines where a
// spelled-out date per row would be noise. Falls back to the raw string.
export function formatTime(value?: string | null): string {
  if (value == null || value === "") return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleTimeString([], { hour12: false });
}
