import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getEvalPolicy, getEvalRun, listEvalRuns } from "@/api/client";
import type { EvalPolicy, EvalRunSummary } from "@/api/client";
import {
  ABSTENTION_PLAIN,
  DEFAULT_EVAL_POLICY,
  METRICS,
  cell,
  qualityBands,
  splitStyle,
  trend,
  type Chip,
  type MetricKey,
} from "@/lib/evalBands";
import { EvalMetricTile } from "@/components/EvalMetricTile";
import { SortableTableHead } from "@/components/SortableTableHead";
import {
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function QualityBandsLegend({ bands }: { bands: EvalPolicy["quality_bands"] | undefined }) {
  const items = qualityBands(bands);
  return (
    <div
      className="flex flex-wrap items-center gap-2.5 rounded-xl border px-4 py-3"
      style={{ background: "oklch(0.955 0.006 95)", borderColor: "oklch(0.88 0.01 95)" }}
    >
      <span
        className="text-[15px] font-semibold uppercase tracking-wide"
        style={{ color: "oklch(0.48 0.025 245)" }}
      >
        Quality bands
      </span>
      {items.map((b) => (
        <span
          key={b.label}
          className="inline-flex items-center gap-1.5 rounded-full py-1 pl-2 pr-2.5 text-[15.625px] font-medium"
          style={{ color: b.color, background: b.bg, border: `1px solid ${b.bd}` }}
        >
          <span className="h-2 w-2 rounded-sm" style={{ background: b.color }} />
          {b.label}
          <span className="font-mono opacity-75">{b.range}</span>
        </span>
      ))}
    </div>
  );
}

function badgePill(children: React.ReactNode, mono = false) {
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-[15px] ${mono ? "font-mono" : ""}`}
      style={{ borderColor: "oklch(0.86 0.014 95)", color: "oklch(0.48 0.025 245)" }}
    >
      {children}
    </span>
  );
}

function HeroCard({ latest, tag }: { latest: ReturnType<typeof buildDetailSummary>; tag: string }) {
  if (!latest) return null;
  return (
    <div
      className="overflow-hidden rounded-2xl bg-card"
      style={{ boxShadow: "0 0 0 1px oklch(0.22 0.018 245 / 0.10)" }}
    >
      <div
        className="flex flex-wrap items-start justify-between gap-4 border-b px-5 pb-4 pt-5"
        style={{ borderColor: "oklch(0.90 0.01 95)" }}
      >
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span
              className="text-[13.75px] font-bold uppercase tracking-wide"
              style={{ color: "oklch(0.42 0.105 158)" }}
            >
              Latest run
            </span>
            <span className="text-[20px] font-semibold">{latest.label || "—"}</span>
          </div>
          <span className="font-mono text-[15px]" style={{ color: "oklch(0.5 0.02 245)" }}>
            {tag}
          </span>
        </div>
        <div className="flex flex-wrap justify-end gap-1.5">
          <span
            className="rounded-full px-2.5 py-0.5 text-[15px] font-medium"
            style={{ background: "oklch(0.92 0.018 88)", color: "oklch(0.31 0.025 245)" }}
          >
            {latest.model || "—"}
          </span>
          {badgePill(latest.date || "—")}
          {badgePill(latest.git || "—", true)}
          {badgePill(`${latest.questions ?? "—"} questions`)}
        </div>
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-5">
        {latest.tiles.map((t) => {
          const { metricKey, ...tileProps } = t;
          return <EvalMetricTile key={metricKey} {...tileProps} scale={1.25} />;
        })}
      </div>
    </div>
  );
}

function buildDetailSummary(
  run: NonNullable<Awaited<ReturnType<typeof getEvalRun>>> | undefined,
  tag: string | undefined,
  bands: EvalPolicy["quality_bands"] | undefined,
) {
  if (!run || !tag) return null;
  const overall = run.summary?.overall;
  const abstention = run.summary?.abstention;
  const tiles: {
    metricKey: string;
    label: string;
    plain: string;
    value: string;
    color: string;
    chip: Chip;
    bandLabel: string;
  }[] = METRICS.map((m) => {
    const c = cell(overall?.[m.key], bands);
    return {
      metricKey: m.key,
      label: m.label,
      plain: m.plain,
      value: c.fmt,
      color: c.color,
      chip: c.chip,
      bandLabel: c.band,
    };
  });
  const abstAccuracy = abstention?.accuracy ?? null;
  const ac = cell(abstAccuracy, bands);
  tiles.push({
    metricKey: "abstention",
    label: "Abstention",
    plain: ABSTENTION_PLAIN,
    value:
      abstention?.correct != null && abstention?.total != null
        ? `${abstention.correct} / ${abstention.total}`
        : ac.fmt,
    color: ac.color,
    chip: ac.chip,
    bandLabel: ac.band,
  });
  return {
    label: run.label,
    date: run.date,
    model: run.model,
    git: run.git_sha,
    questions: run.question_count,
    tiles,
  };
}

type AllRunsSortKey = "run" | "split" | MetricKey | "abstention";

function AllRunsTable({
  runs,
  bands,
  noiseFloor,
}: {
  runs: EvalRunSummary[];
  bands: EvalPolicy["quality_bands"] | undefined;
  noiseFloor: number;
}) {
  const [sortKey, setSortKey] = useState<AllRunsSortKey | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const chronological = useMemo(
    () =>
      [...runs]
        .filter((r) => !r.holdout)
        .sort((a, b) => (a.date ?? "").localeCompare(b.date ?? "") || a.tag.localeCompare(b.tag)),
    [runs],
  );
  const prevOf = (run: EvalRunSummary): EvalRunSummary | null => {
    if (run.holdout) return null;
    const i = chronological.findIndex((r) => r.tag === run.tag);
    return i > 0 ? (chronological[i - 1] ?? null) : null;
  };
  const latestNonHoldout = chronological[chronological.length - 1];

  const toggleSort = (key: AllRunsSortKey) => {
    if (sortKey !== key) {
      setSortKey(key);
      setSortDir(key === "run" || key === "split" ? "asc" : "desc");
      return;
    }
    setSortDir((d) => (d === "asc" ? "desc" : "asc"));
  };

  const sortedRuns = useMemo(() => {
    if (!sortKey) return runs;
    const sign = sortDir === "asc" ? 1 : -1;
    const withNullsLast = (a: number | null | undefined, b: number | null | undefined) => {
      if (a == null && b == null) return 0;
      if (a == null) return 1;
      if (b == null) return -1;
      return (a - b) * sign;
    };
    return [...runs].sort((a, b) => {
      if (sortKey === "run") return sign * (a.label || a.tag).localeCompare(b.label || b.tag);
      if (sortKey === "split") {
        return sign * Number(a.holdout) - sign * Number(b.holdout) || (a.label || a.tag).localeCompare(b.label || b.tag);
      }
      if (sortKey === "abstention") return withNullsLast(a.abstention_accuracy, b.abstention_accuracy);
      return withNullsLast(a[sortKey], b[sortKey]);
    });
  }, [runs, sortKey, sortDir]);

  return (
    <div className="overflow-hidden rounded-2xl bg-card" style={{ boxShadow: "0 0 0 1px oklch(0.22 0.018 245 / 0.10)" }}>
      <div className="flex items-baseline justify-between px-5 pb-3 pt-4">
        <span className="text-[18.75px] font-semibold">All runs</span>
        <span className="text-[15px]" style={{ color: "oklch(0.5 0.02 245)" }}>
          arrows compare each run to the one before ·{" "}
          <span style={{ color: "oklch(0.55 0.02 245)" }}>≈ = within judge noise</span> · click a column to sort
        </span>
      </div>
      <div className="max-h-[70vh] overflow-auto [&_[data-slot=table-container]]:overflow-visible">
        <Table className="border-separate border-spacing-0 text-[17.5px]">
          <TableHeader>
            <TableRow style={{ background: "oklch(0.975 0.006 95)" }}>
              <SortableTableHead
                label="Run"
                fontSize={17.5}
                active={sortKey === "run"}
                dir={sortDir}
                onClick={() => toggleSort("run")}
              />
              <SortableTableHead
                label="Split"
                fontSize={17.5}
                active={sortKey === "split"}
                dir={sortDir}
                onClick={() => toggleSort("split")}
              />
              {METRICS.map((m) => (
                <SortableTableHead
                  key={m.key}
                  label={m.short}
                  fontSize={17.5}
                  align="right"
                  active={sortKey === m.key}
                  dir={sortDir}
                  onClick={() => toggleSort(m.key)}
                />
              ))}
              <SortableTableHead
                label="Abstention"
                fontSize={17.5}
                align="right"
                active={sortKey === "abstention"}
                dir={sortDir}
                onClick={() => toggleSort("abstention")}
              />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedRuns.map((run) => {
              const prev = prevOf(run);
              const ss = splitStyle(run.holdout);
              const abstC = cell(run.abstention_accuracy, bands);
              return (
                <TableRow key={run.tag}>
                  <TableCell className="align-top">
                    <Link
                      to={`/evals/${encodeURIComponent(run.tag)}`}
                      className="flex flex-col gap-0.5 text-inherit no-underline hover:underline"
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="text-[16.875px] font-semibold">{run.label || run.tag}</span>
                        {run.tag === latestNonHoldout?.tag && (
                          <span
                            className="rounded-full px-1.5 py-px text-[12.5px] font-bold uppercase tracking-wide"
                            style={{ color: "oklch(0.42 0.105 158)", background: "oklch(0.955 0.035 158)" }}
                          >
                            latest
                          </span>
                        )}
                        {run.holdout && (
                          <span title="Sealed — aggregate only" className="text-[13.75px]" style={{ color: "oklch(0.45 0.06 300)" }}>
                            🔒 sealed
                          </span>
                        )}
                      </div>
                      <span className="font-mono text-[13.75px]" style={{ color: "oklch(0.55 0.02 245)" }}>
                        {run.date ?? "—"} · {run.git_sha ?? "—"}
                      </span>
                    </Link>
                  </TableCell>
                  <TableCell className="align-top">
                    <span
                      className="whitespace-nowrap rounded-full px-2.5 py-0.5 text-[14.375px] font-medium"
                      style={{ color: ss.color, background: ss.bg, border: `1px solid ${ss.bd}` }}
                    >
                      {run.holdout ? "Holdout" : "Regression + Dev"}
                    </span>
                  </TableCell>
                  {METRICS.map((m) => {
                    const c = cell(run[m.key], bands);
                    const t = trend(run[m.key], prev ? prev[m.key] : null, noiseFloor);
                    return (
                      <TableCell key={m.key} className="text-right align-top">
                        <span className="inline-flex items-center justify-end gap-1.5">
                          {t.show && (
                            <span title={t.title} className="text-[13.75px]" style={{ color: t.color }}>
                              {t.sym}
                            </span>
                          )}
                          <span
                            className="font-mono rounded-md px-2 py-0.5 text-[15.625px] font-semibold"
                            style={{ color: c.chip.color, background: c.chip.background, border: c.chip.border }}
                          >
                            {c.fmt}
                          </span>
                        </span>
                      </TableCell>
                    );
                  })}
                  <TableCell className="text-right align-top">
                    <span
                      className="font-mono rounded-md px-2 py-0.5 text-[15.625px] font-semibold"
                      style={{ color: abstC.chip.color, background: abstC.chip.background, border: abstC.chip.border }}
                    >
                      {abstC.fmt}
                    </span>
                  </TableCell>
                </TableRow>
              );
            })}
            {runs.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="text-muted-foreground">
                  No eval runs found.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function SplitsExplainer({ splits }: { splits: EvalPolicy["splits"] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {splits.map((sp) => (
        <div
          key={sp.key}
          className="flex flex-col gap-1.5 rounded-xl bg-card p-4"
          style={{ boxShadow: "0 0 0 1px oklch(0.22 0.018 245 / 0.08)" }}
        >
          <div className="flex items-center gap-2">
            <span className="text-[17.5px] font-semibold">{sp.name}</span>
            <span
              className="font-mono rounded-full px-1.5 py-px text-[14.375px]"
              style={{ color: "oklch(0.5 0.02 245)", background: "oklch(0.955 0.006 95)" }}
            >
              {sp.count} rows
            </span>
          </div>
          <p className="m-0 text-[15px] leading-relaxed" style={{ color: "oklch(0.44 0.02 245)" }}>
            {sp.plain}
          </p>
        </div>
      ))}
    </div>
  );
}

export default function Evals() {
  const runsQuery = useQuery({ queryKey: ["evalRuns"], queryFn: listEvalRuns });
  const policyQuery = useQuery({ queryKey: ["evalPolicy"], queryFn: getEvalPolicy });
  const policy = policyQuery.data ?? DEFAULT_EVAL_POLICY;
  const runs = runsQuery.data?.runs ?? [];
  const latestTag = useMemo(() => runs.find((r) => !r.holdout)?.tag, [runs]);
  const latestRunQuery = useQuery({
    queryKey: ["evalRun", latestTag],
    queryFn: () => getEvalRun(latestTag!),
    enabled: Boolean(latestTag),
  });
  const heroSummary = buildDetailSummary(latestRunQuery.data, latestTag, policy.quality_bands);

  if (runsQuery.isLoading) return <p>Loading…</p>;
  if (runsQuery.error) return <p className="text-red-600">Failed to load eval runs.</p>;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1.5">
        <h1 className="m-0 text-[32.5px] font-semibold tracking-tight">Evaluations</h1>
        <p className="m-0 max-w-[640px] text-[17.5px]" style={{ color: "oklch(0.48 0.025 245)" }}>
          Each run grades the system against a fixed exam of legal questions. Higher is better; the bands below
          tell you whether a score is actually good.
        </p>
      </div>

      <QualityBandsLegend bands={policy.quality_bands} />

      {latestTag && <HeroCard latest={heroSummary} tag={latestTag} />}

      <AllRunsTable runs={runs} bands={policy.quality_bands} noiseFloor={policy.noise_floor} />

      <SplitsExplainer splits={policy.splits} />
    </div>
  );
}
