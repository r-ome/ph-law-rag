import { TableHead } from "@/components/ui/table";

export function SortableTableHead({
  label,
  active,
  dir,
  align = "left",
  fontSize,
  uppercase = false,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  align?: "left" | "right";
  /** Base font-size (px) for the label; the sort arrow scales relative to it. */
  fontSize?: number;
  /** Uppercase, letter-spaced label style (per-question rows table). */
  uppercase?: boolean;
  onClick: () => void;
}) {
  return (
    <TableHead
      className={`sticky top-0 z-10 shadow-[inset_0_-1px_0_oklch(0.90_0.01_95)] ${align === "right" ? "text-right" : ""}`}
      style={{ background: "oklch(0.975 0.006 95)", fontSize: fontSize ? `${fontSize}px` : undefined }}
    >
      <button
        type="button"
        onClick={onClick}
        aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
        className={`inline-flex cursor-pointer select-none items-center gap-1 font-medium ${
          align === "right" ? "flex-row-reverse" : ""
        } ${uppercase ? "uppercase tracking-[0.08em]" : ""}`}
        style={{
          color: active ? "oklch(0.28 0.02 245)" : uppercase ? "oklch(0.50 0.02 245)" : "inherit",
        }}
      >
        {label}
        <span style={{ opacity: active ? 1 : 0.35, fontSize: fontSize ? `${fontSize * 0.85}px` : undefined }}>
          {active ? (dir === "asc" ? "▲" : "▼") : uppercase ? "↕" : "▲"}
        </span>
      </button>
    </TableHead>
  );
}
