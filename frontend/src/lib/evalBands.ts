export type BandKey = "strong" | "fair" | "weak" | "na";

export type Band = {
  key: BandKey;
  label: string;
  color: string;
  bg: string;
  bd: string;
};

export type Chip = { color: string; background: string; border: string };

export type Cell = {
  fmt: string;
  band: string;
  bandKey: BandKey;
  color: string;
  chip: Chip;
};

export type Trend = {
  show: boolean;
  sym: string;
  color: string;
  title: string;
  deltaFmt: string;
};

export type SplitStyle = { color: string; bg: string; bd: string };

const NOISE_FLOOR_DEFAULT = 0.05;

export function fmt(v: number | null | undefined): string {
  return v == null ? "—" : v.toFixed(3);
}

export function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

export function band(v: number | null | undefined): Band {
  if (v == null) {
    return {
      key: "na",
      label: "n/a",
      color: "oklch(0.52 0.02 245)",
      bg: "oklch(0.955 0.006 95)",
      bd: "oklch(0.88 0.01 95)",
    };
  }
  if (v >= 0.85) {
    return {
      key: "strong",
      label: "Strong",
      color: "oklch(0.40 0.10 158)",
      bg: "oklch(0.955 0.035 158)",
      bd: "oklch(0.85 0.06 158)",
    };
  }
  if (v >= 0.7) {
    return {
      key: "fair",
      label: "Fair",
      color: "oklch(0.48 0.10 78)",
      bg: "oklch(0.955 0.05 88)",
      bd: "oklch(0.86 0.07 85)",
    };
  }
  return {
    key: "weak",
    label: "Weak",
    color: "oklch(0.52 0.17 32)",
    bg: "oklch(0.955 0.05 38)",
    bd: "oklch(0.86 0.08 36)",
  };
}

export function cell(v: number | null | undefined): Cell {
  const b = band(v);
  return {
    fmt: fmt(v),
    band: b.label,
    bandKey: b.key,
    color: b.color,
    chip: { color: b.color, background: b.bg, border: `1px solid ${b.bd}` },
  };
}

export function trend(
  cur: number | null | undefined,
  prev: number | null | undefined,
  noiseFloor: number = NOISE_FLOOR_DEFAULT,
): Trend {
  if (prev == null || cur == null) {
    return { show: false, sym: "", color: "", title: "", deltaFmt: "" };
  }
  const d = cur - prev;
  const within = Math.abs(d) < noiseFloor;
  const deltaFmt = (d >= 0 ? "+" : "−") + Math.abs(d).toFixed(3);
  if (within) {
    return {
      show: true,
      sym: "≈",
      color: "oklch(0.55 0.02 245)",
      title: `${deltaFmt} vs previous run · within judge noise`,
      deltaFmt,
    };
  }
  if (d > 0) {
    return {
      show: true,
      sym: "▲",
      color: "oklch(0.42 0.11 158)",
      title: `${deltaFmt} vs previous run`,
      deltaFmt,
    };
  }
  return {
    show: true,
    sym: "▼",
    color: "oklch(0.55 0.19 30)",
    title: `${deltaFmt} vs previous run`,
    deltaFmt,
  };
}

export function splitStyle(holdout: boolean): SplitStyle {
  if (holdout) {
    return { color: "oklch(0.45 0.06 300)", bg: "oklch(0.955 0.03 305)", bd: "oklch(0.86 0.05 305)" };
  }
  return { color: "oklch(0.40 0.10 158)", bg: "oklch(0.955 0.03 158)", bd: "oklch(0.86 0.05 158)" };
}

export const NOISE_FLOOR = NOISE_FLOOR_DEFAULT;
export const NOISE_FLOOR_FMT = NOISE_FLOOR_DEFAULT.toFixed(2);

export const NOISE_NOTE =
  "RAGAS grades answers with an LLM judge. On byte-identical inputs, faithfulness alone can swing ±0.25. " +
  "Treat any move smaller than the noise floor as no change — trust paired, same-question deltas instead.";

export type MetricKey = "faithfulness" | "answer_relevancy" | "context_precision" | "context_recall";

export function verdictChip(verdict: string | null | undefined): Chip {
  if (verdict === "sufficient") {
    return { color: "oklch(0.40 0.10 158)", background: "oklch(0.955 0.035 158)", border: "1px solid oklch(0.85 0.06 158)" };
  }
  if (verdict === "partial") {
    return { color: "oklch(0.48 0.10 78)", background: "oklch(0.955 0.05 88)", border: "1px solid oklch(0.86 0.07 85)" };
  }
  if (verdict === "insufficient") {
    return { color: "oklch(0.52 0.17 32)", background: "oklch(0.955 0.05 38)", border: "1px solid oklch(0.86 0.08 36)" };
  }
  return { color: "oklch(0.52 0.02 245)", background: "oklch(0.955 0.006 95)", border: "1px solid oklch(0.88 0.01 95)" };
}

export const METRICS: { key: MetricKey; label: string; short: string; plain: string }[] = [
  {
    key: "faithfulness",
    label: "Faithfulness",
    short: "Faith",
    plain: "Does every claim in the answer trace back to a retrieved source? High means the model invented no law.",
  },
  {
    key: "answer_relevancy",
    label: "Relevancy",
    short: "Rel",
    plain: "Does the answer actually address what was asked, without padding or drifting off-topic?",
  },
  {
    key: "context_precision",
    label: "Precision",
    short: "Prec",
    plain: "Of the passages retrieved, how many were genuinely relevant? Low means the model had to wade through noise.",
  },
  {
    key: "context_recall",
    label: "Recall",
    short: "Rec",
    plain: "Did retrieval surface all the passages needed to answer? Low means something the answer needed was missed.",
  },
];

export const ABSTENTION_PLAIN =
  'When the corpus cannot support an answer, does the system say "I don\'t know" instead of guessing? ' +
  "A confident wrong answer is the worst outcome in a legal tool.";

export const SPLITS: { key: string; name: string; count: number; plain: string }[] = [
  {
    key: "regression",
    name: "Regression",
    count: 81,
    plain:
      "Frozen benchmark — 81 questions, hash-locked so nobody edits them quietly. The standing scoreboard used to compare runs over time.",
  },
  {
    key: "dev",
    name: "Dev",
    count: 50,
    plain: "Practice set — 50 questions we study and tune against, so scores here run a little flattering.",
  },
  {
    key: "holdout",
    name: "Holdout",
    count: 30,
    plain:
      'Sealed final exam — 30 questions. Only the total is ever read; inspecting one row "burns" it. The one honest test of generalization.',
  },
];

export const QUALITY_BANDS = [
  { ...band(0.9), range: "≥ 0.85" },
  { ...band(0.78), range: "0.70 – 0.85" },
  { ...band(0.5), range: "< 0.70" },
];
