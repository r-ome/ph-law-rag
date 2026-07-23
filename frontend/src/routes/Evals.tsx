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
import { PageHeader } from "@/components/ui/page-header";
import { formatDate } from "@/lib/format";
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
    <div className="flex flex-wrap items-center gap-2.5 rounded-xl border border-border bg-muted px-4 py-3">
      <span className="text-[11px] font-semibold tracking-[0.08em] text-muted-foreground uppercase">
        Quality bands
      </span>
      {items.map((b) => (
        <span
          key={b.label}
          className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-xs font-medium"
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
      className={`rounded-full border border-border bg-muted px-2.5 py-[3px] text-[11.5px] text-muted-foreground ${mono ? "font-mono" : ""}`}
    >
      {children}
    </span>
  );
}

function HeroCard({ latest, tag }: { latest: ReturnType<typeof buildDetailSummary>; tag: string }) {
  if (!latest) return null;
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-[var(--shadow)]">
      <div className="flex flex-wrap items-start justify-between gap-3.5 border-b border-border px-5 py-4">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-bold tracking-[0.06em] text-primary uppercase">
              Latest run
            </span>
            <span className="font-serif text-[19px] font-semibold">{latest.label || "—"}</span>
          </div>
          <span className="mt-0.5 font-mono text-xs text-faint">{tag}</span>
        </div>
        <div className="flex flex-wrap justify-end gap-1.5">
          <span className="rounded-full bg-muted px-2.5 py-[3px] text-[11.5px] font-medium">
            {latest.model || "—"}
          </span>
          {badgePill(formatDate(latest.date))}
          {badgePill(latest.git || "—", true)}
          {badgePill(`${latest.questions ?? "—"} questions`)}
        </div>
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-5">
        {latest.tiles.map((t) => {
          const { metricKey, ...tileProps } = t;
          return <EvalMetricTile key={metricKey} {...tileProps} scale={1} />;
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
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-[var(--shadow)]">
      <div className="flex items-baseline justify-between border-b border-border px-[18px] py-3.5">
        <span className="font-serif text-base font-semibold">All runs</span>
        <span className="text-[11.5px] text-faint">
          arrows compare each run to the one before · ≈ = within judge noise · click a column to sort
        </span>
      </div>
      <div className="max-h-[70vh] overflow-auto [&_[data-slot=table-container]]:overflow-visible">
        <Table className="border-separate border-spacing-0">
          <TableHeader>
            <TableRow className="bg-muted">
              <SortableTableHead
                label="Run"
                fontSize={11}
                active={sortKey === "run"}
                dir={sortDir}
                onClick={() => toggleSort("run")}
              />
              <SortableTableHead
                label="Split"
                fontSize={11}
                active={sortKey === "split"}
                dir={sortDir}
                onClick={() => toggleSort("split")}
              />
              {METRICS.map((m) => (
                <SortableTableHead
                  key={m.key}
                  label={m.short}
                  fontSize={11}
                  align="right"
                  active={sortKey === m.key}
                  dir={sortDir}
                  onClick={() => toggleSort(m.key)}
                />
              ))}
              <SortableTableHead
                label="Abstention"
                fontSize={11}
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
                        <span className="text-[13px] font-semibold text-primary">{run.label || run.tag}</span>
                        {run.tag === latestNonHoldout?.tag && (
                          <span className="rounded-full bg-primary-bg px-1.5 py-px text-[12.5px] font-bold text-primary uppercase tracking-wide">
                            latest
                          </span>
                        )}
                        {run.holdout && (
                          <span title="Sealed — aggregate only" className="text-[13.75px] text-violet">
                            🔒 sealed
                          </span>
                        )}
                      </div>
                      <span className="font-mono text-[10.5px] text-faint">
                        {formatDate(run.date)} · {run.git_sha ?? "—"}
                      </span>
                    </Link>
                  </TableCell>
                  <TableCell className="align-top">
                    <span
                      className="whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-medium"
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
                            <span title={t.title} className="text-[10px]" style={{ color: t.color }}>
                              {t.sym}
                            </span>
                          )}
                          <span
                            className="font-mono rounded-md px-2 py-0.5 text-[12px] font-semibold"
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
                      className="font-mono rounded-md px-2 py-0.5 text-[12px] font-semibold"
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
        <div key={sp.key} className="flex flex-col gap-1.5 rounded-xl border border-border bg-card p-4">
          <div className="flex items-center gap-2">
            <span className="font-serif text-sm font-semibold">{sp.name}</span>
            <span className="font-mono rounded-full bg-muted px-1.5 py-px text-[11px] text-faint">
              {sp.count} rows
            </span>
          </div>
          <p className="m-0 text-[13px] leading-relaxed text-muted-foreground">{sp.plain}</p>
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

  if (runsQuery.isLoading) return <p className="text-muted-foreground">Loading…</p>;
  if (runsQuery.error) return <p className="text-danger">Failed to load eval runs.</p>;

  return (
    <div className="mx-auto flex max-w-[1240px] flex-col gap-4">
      <PageHeader
        eyebrow="RAGAS quality"
        title="Evaluations"
        subtitle="Each run grades the system against a fixed exam of legal questions. Higher is better; the bands below tell you whether a score is actually good."
      />

      <QualityBandsLegend bands={policy.quality_bands} />

      {latestTag && <HeroCard latest={heroSummary} tag={latestTag} />}

      <AllRunsTable runs={runs} bands={policy.quality_bands} noiseFloor={policy.noise_floor} />

      <SplitsExplainer splits={policy.splits} />
    </div>
  );
}
