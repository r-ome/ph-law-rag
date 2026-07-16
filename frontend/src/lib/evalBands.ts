import type { EvalPolicy } from "@/api/client";
import { fmtDelta, fmtMetric } from "@/lib/metrics";

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

export type QualityBandPolicy = EvalPolicy["quality_bands"][number];

export const DEFAULT_EVAL_POLICY: EvalPolicy = {
  noise_floor: 0.05,
  quality_bands: [
    { key: "strong", label: "Strong", min: 0.85, range: ">= 0.85" },
    { key: "fair", label: "Fair", min: 0.7, range: "0.70 - 0.85" },
    { key: "weak", label: "Weak", min: null, range: "< 0.70" },
  ],
  splits: [],
};

export function fmt(v: number | null | undefined): string {
  return fmtMetric(v);
}

export function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function bandVisual(key: BandKey, label: string): Band {
  if (key === "strong") {
    return {
      key,
      label,
      color: "oklch(0.40 0.10 158)",
      bg: "oklch(0.955 0.035 158)",
      bd: "oklch(0.85 0.06 158)",
    };
  }
  if (key === "fair") {
    return {
      key,
      label,
      color: "oklch(0.48 0.10 78)",
      bg: "oklch(0.955 0.05 88)",
      bd: "oklch(0.86 0.07 85)",
    };
  }
  if (key === "weak") {
    return {
      key,
      label,
      color: "oklch(0.52 0.17 32)",
      bg: "oklch(0.955 0.05 38)",
      bd: "oklch(0.86 0.08 36)",
    };
  }
  return {
    key,
    label,
    color: "oklch(0.52 0.02 245)",
    bg: "oklch(0.955 0.006 95)",
    bd: "oklch(0.88 0.01 95)",
  };
}

export function band(
  v: number | null | undefined,
  bands: QualityBandPolicy[] = DEFAULT_EVAL_POLICY.quality_bands,
): Band {
  if (v == null) {
    return bandVisual("na", "n/a");
  }
  const sortedBands = [...bands].sort((a, b) => (b.min ?? -Infinity) - (a.min ?? -Infinity));
  const selected = sortedBands.find((candidate) => candidate.min != null && v >= candidate.min)
    ?? sortedBands.find((candidate) => candidate.min == null)
    ?? DEFAULT_EVAL_POLICY.quality_bands[DEFAULT_EVAL_POLICY.quality_bands.length - 1]!;
  return bandVisual(selected.key, selected.label);
}

export function cell(v: number | null | undefined, bands?: QualityBandPolicy[]): Cell {
  const b = band(v, bands);
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
  noiseFloor: number = DEFAULT_EVAL_POLICY.noise_floor,
): Trend {
  if (prev == null || cur == null) {
    return { show: false, sym: "", color: "", title: "", deltaFmt: "" };
  }
  const d = cur - prev;
  const within = Math.abs(d) < noiseFloor;
  const deltaFmt = fmtDelta(d);
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

export const NOISE_NOTE =
  "RAGAS grades answers with an LLM judge. On byte-identical inputs, faithfulness alone can swing ±0.25. " +
  "Treat any move smaller than the noise floor as no change — trust paired, same-question deltas instead.";

export type MetricKey = "faithfulness" | "answer_relevancy" | "context_precision" | "context_recall";

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

export function qualityBands(bands: QualityBandPolicy[] = DEFAULT_EVAL_POLICY.quality_bands) {
  return bands.map((policy) => ({ ...bandVisual(policy.key, policy.label), range: policy.range }));
}
