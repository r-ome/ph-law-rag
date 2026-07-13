import { useState } from "react";
import type { Chip } from "@/lib/evalBands";

export function EvalMetricTile({
  label,
  plain,
  value,
  color,
  chip,
  bandLabel,
  sub,
  scale = 1,
}: {
  label: string;
  plain: string;
  value: string;
  color: string;
  chip: Chip;
  bandLabel: string;
  sub?: string;
  /** Font-size multiplier applied to all text in the tile (default 1 = unscaled). */
  scale?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const px = (base: number) => `${base * scale}px`;
  return (
    <div className="flex flex-col gap-2 rounded-xl bg-card p-4 ring-1 ring-foreground/10">
      <div className="flex items-center justify-between">
        <span className="font-medium" style={{ color: "oklch(0.42 0.02 245)", fontSize: px(13) }}>
          {label}
        </span>
        <button
          type="button"
          aria-label={`What is ${label}?`}
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="flex h-[19px] w-[19px] cursor-pointer items-center justify-center rounded-full border font-bold leading-none"
          style={{
            borderColor: "oklch(0.84 0.014 95)",
            background: "oklch(0.97 0.006 95)",
            color: "oklch(0.5 0.02 245)",
            fontSize: px(11),
          }}
        >
          ?
        </button>
      </div>
      <span className="font-mono font-semibold tracking-tight" style={{ color, fontSize: px(24) }}>
        {value}
      </span>
      {sub && (
        <span className="text-muted-foreground" style={{ fontSize: px(12) }}>
          {sub}
        </span>
      )}
      <span
        className="self-start rounded-full px-2.5 py-0.5 font-semibold"
        style={{ color: chip.color, background: chip.background, border: chip.border, fontSize: px(11) }}
      >
        {bandLabel}
      </span>
      {expanded && (
        <p className="m-0 leading-relaxed text-muted-foreground" style={{ fontSize: px(12) }}>
          {plain}
        </p>
      )}
    </div>
  );
}
