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
    <div
      className="flex flex-col gap-1.5 rounded-xl p-3.5"
      style={{ border: chip.border, background: chip.background }}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className="font-semibold tracking-[0.04em] text-muted-foreground uppercase"
          style={{ fontSize: px(11.5) }}
        >
          {label}
        </span>
        <button
          type="button"
          aria-label={`What is ${label}?`}
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="flex size-[18px] cursor-pointer items-center justify-center rounded-full border border-border bg-card font-bold leading-none text-faint"
          style={{ fontSize: px(10) }}
        >
          ?
        </button>
      </div>
      <span
        className="font-mono font-semibold tracking-[-0.02em]"
        style={{ color, fontSize: px(27) }}
      >
        {value}
      </span>
      {sub ? (
        <span className="text-faint" style={{ fontSize: px(11) }}>
          {sub}
        </span>
      ) : null}
      <span className="font-semibold" style={{ color, fontSize: px(11) }}>
        {bandLabel}
      </span>
      {expanded ? (
        <p className="m-0 leading-relaxed text-muted-foreground" style={{ fontSize: px(11.5) }}>
          {plain}
        </p>
      ) : null}
    </div>
  );
}
