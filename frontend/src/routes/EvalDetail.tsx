import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getEvalDiff, getEvalPolicy, getEvalRows, getEvalRun, listEvalRuns } from "@/api/client";
import type { EvalDiff, EvalPolicy, EvalRow, EvalRunDetail } from "@/api/client";
import {
  ABSTENTION_PLAIN,
  DEFAULT_EVAL_POLICY,
  METRICS,
  NOISE_NOTE,
  cell,
  splitStyle,
  trend,
  type MetricKey,
} from "@/lib/evalBands";
import { defaultRowFilters, filterRows, type RowFilters } from "@/lib/evalRows";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EvalMetricTile } from "@/components/EvalMetricTile";
import { SortableTableHead } from "@/components/SortableTableHead";
import EvalRowSheet from "@/components/EvalRowSheet";
import EvalRunLogs from "@/components/EvalRunLogs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function Pill({ children, mono = false }: { children: React.ReactNode; mono?: boolean }) {
  return (
    <span
      className={`rounded-full border border-border bg-muted px-2.5 py-[3px] text-[11.5px] text-muted-foreground ${mono ? "font-mono" : ""}`}
    >
      {children}
    </span>
  );
}

function MetricChip({
  chip,
  children,
  fontSize = 12.5,
}: {
  chip: { color: string; background: string; border: string };
  children: React.ReactNode;
  fontSize?: number;
}) {
  return (
    <span
      className="font-mono rounded-md px-2 py-0.5 font-semibold"
      style={{ color: chip.color, background: chip.background, border: chip.border, fontSize: `${fontSize}px` }}
    >
      {children}
    </span>
  );
}

function ConfigFlag({ label, value }: { label: string; value: boolean | undefined }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {label}
      <Badge variant={value ? "default" : "outline"}>
        {value === undefined ? "n/a" : value ? "on" : "off"}
      </Badge>
    </span>
  );
}

function RunConfig({ run }: { run: EvalRunDetail }) {
  const cfg = (run.meta?.active_config ?? {}) as Record<string, unknown>;
  const str = (key: string) => {
    const v = cfg[key];
    return typeof v === "string" && v ? v : "—";
  };
  const bool = (key: string) => (typeof cfg[key] === "boolean" ? (cfg[key] as boolean) : undefined);

  if (!run.meta?.active_config) return null;

  const overrides = (cfg.policy_overrides ?? {}) as Record<string, unknown>;
  const evidenceJudge =
    typeof overrides.evidence_judge_model === "string" && overrides.evidence_judge_model
      ? overrides.evidence_judge_model
      : str("evidence_judge_model");

  const rows: [string, string][] = [
    ["Profile", str("profile")],
    ["Generator model", str("llm_model")],
    ["Embedding model", str("embedding_model")],
    ["Reranker", str("reranker_backend")],
    ["Evidence gate", str("evidence_gate")],
    ["Evidence judge", evidenceJudge],
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Run Config</CardTitle>
        <CardDescription>Effective pipeline config at generation time.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm md:grid-cols-2 xl:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="grid gap-1">
            <span className="text-muted-foreground">{label}</span>
            <span className="break-all font-medium">{value}</span>
          </div>
        ))}
        <div className="flex flex-wrap gap-4 md:col-span-2 xl:col-span-3">
          <ConfigFlag label="CRAG" value={cfg.evidence_gate === "crag"} />
          <ConfigFlag label="Corrective" value={bool("corrective_retrieval_enabled")} />
        </div>
      </CardContent>
    </Card>
  );
}

function SummaryTiles({ run, bands }: { run: EvalRunDetail; bands: EvalPolicy["quality_bands"] | undefined }) {
  const overall = run.summary?.overall;
  const abstention = run.summary?.abstention;
  const tiles = METRICS.map((m) => {
    const c = cell(overall?.[m.key], bands);
    return {
      metricKey: m.key as string,
      label: m.label,
      plain: m.plain,
      value: c.fmt,
      color: c.color,
      chip: c.chip,
      bandLabel: c.band,
    };
  });
  const ac = cell(abstention?.accuracy ?? null, bands);
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

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      {tiles.map((t) => {
        const { metricKey, ...tileProps } = t;
        return <EvalMetricTile key={metricKey} {...tileProps} />;
      })}
    </div>
  );
}

function HowToRead({ noiseFloor }: { noiseFloor: number }) {
  return (
    <div className="flex gap-3 rounded-xl bg-[var(--sidebar-bg)] px-[17px] py-3.5 text-[var(--sidebar-fg)]">
      <span className="shrink-0 font-serif text-lg font-bold text-gold">§</span>
      <div className="flex flex-col gap-0.5">
        <span className="text-[13px] font-semibold">How to read these scores</span>
        <p className="m-0 max-w-[900px] text-xs leading-relaxed text-[var(--sidebar-muted)]">
          {NOISE_NOTE} Current noise floor: ±{noiseFloor.toFixed(2)}.
        </p>
      </div>
    </div>
  );
}

function ByCategory({ run, bands }: { run: EvalRunDetail; bands: EvalPolicy["quality_bands"] | undefined }) {
  const categories = Object.entries(run.summary?.by_category ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          By category{" "}
          <span className="text-sm font-normal text-muted-foreground">
            — where the system is strong and where it slips
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Category</TableHead>
              <TableHead className="text-right">n</TableHead>
              {METRICS.map((m) => (
                <TableHead key={m.key} className="text-right">
                  {m.short}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {categories.map(([category, metrics]) => (
              <TableRow key={category}>
                <TableCell className="font-medium">{category}</TableCell>
                <TableCell className="text-right font-mono text-muted-foreground">{metrics.n ?? "—"}</TableCell>
                {METRICS.map((m) => {
                  const c = cell(metrics[m.key], bands);
                  return (
                    <TableCell key={m.key} className="text-right">
                      <MetricChip chip={c.chip}>{c.fmt}</MetricChip>
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
            {categories.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground">
                  No category metrics.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function ComparePanel({
  tag,
  bands,
  noiseFloor,
}: {
  tag: string;
  bands: EvalPolicy["quality_bands"] | undefined;
  noiseFloor: number;
}) {
  const [baseline, setBaseline] = useState<string | undefined>();
  const runsQuery = useQuery({ queryKey: ["evalRuns"], queryFn: listEvalRuns });
  const diffQuery = useQuery({
    queryKey: ["evalDiff", tag, baseline],
    queryFn: () => getEvalDiff(tag, baseline!),
    enabled: Boolean(baseline),
  });
  const candidates = (runsQuery.data?.runs ?? []).filter((run) => run.tag !== tag);
  const diff = diffQuery.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Compare against a baseline</CardTitle>
        <CardDescription>
          Pick an earlier run to see the change. Deltas smaller than the noise floor are flagged ≈.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {candidates.length > 0 ? (
          <Select value={baseline ?? ""} onValueChange={(value) => setBaseline(value || undefined)}>
            <SelectTrigger className="w-full max-w-[420px]" aria-label="Baseline">
              <SelectValue placeholder="Select baseline" />
            </SelectTrigger>
            <SelectContent>
              {candidates.map((run) => (
                <SelectItem key={run.tag} value={run.tag}>
                  {run.label ? `${run.label} · ${run.date ?? ""}` : run.tag}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <p className="text-sm text-muted-foreground">No other runs available for comparison.</p>
        )}
        {diffQuery.isLoading && <p>Loading comparison…</p>}
        {diffQuery.error && <p className="text-sm text-danger">Failed to load comparison.</p>}
        {diff && <DiffTables diff={diff} bands={bands} noiseFloor={noiseFloor} />}
      </CardContent>
    </Card>
  );
}

function DiffTables({
  diff,
  bands,
  noiseFloor,
}: {
  diff: EvalDiff;
  bands: EvalPolicy["quality_bands"] | undefined;
  noiseFloor: number;
}) {
  const cats = Object.entries(diff.by_category).sort(([a], [b]) => a.localeCompare(b));
  const overallRows = [
    ...METRICS.map((m) => ({
      label: m.label,
      cand: diff.overall.candidate[m.key],
      base: diff.overall.baseline[m.key],
    })),
    { label: "Abstention", cand: diff.abstention.candidate, base: diff.abstention.baseline },
  ];
  const anyWithin = overallRows.some(
    (r) => r.cand != null && r.base != null && Math.abs(r.cand - r.base) < noiseFloor,
  );

  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Metric</TableHead>
              <TableHead className="text-right">This run</TableHead>
              <TableHead className="text-right">Baseline</TableHead>
              <TableHead className="text-right">Δ</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {overallRows.map((row) => {
              const c = cell(row.cand, bands);
              const b = cell(row.base, bands);
              const t = trend(row.cand, row.base, noiseFloor);
              return (
                <TableRow key={row.label}>
                  <TableCell>{row.label}</TableCell>
                  <TableCell className="text-right">
                    <MetricChip chip={c.chip}>{c.fmt}</MetricChip>
                  </TableCell>
                  <TableCell className="text-right">
                    <MetricChip chip={b.chip}>{b.fmt}</MetricChip>
                  </TableCell>
                  <TableCell className="text-right">
                    {t.show ? (
                      <span className="font-mono font-semibold" style={{ color: t.color }}>
                        {t.sym} {t.deltaFmt}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">n/a</span>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Category</TableHead>
              <TableHead>Status</TableHead>
              {METRICS.map((m) => (
                <TableHead key={m.key} className="text-right">
                  {m.short} Δ
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {cats.map(([category, row]) => (
              <TableRow key={category}>
                <TableCell>{category}</TableCell>
                <TableCell>
                  <Badge variant={row.status === "matched" ? "secondary" : "outline"}>
                    {row.status}
                  </Badge>
                </TableCell>
                {METRICS.map((m) => {
                  const t = row.delta ? trend(row.candidate?.[m.key], row.baseline?.[m.key], noiseFloor) : null;
                  return (
                    <TableCell key={m.key} className="text-right">
                      {t?.show ? (
                        <span className="font-mono font-semibold" style={{ color: t.color }}>
                          {t.sym} {t.deltaFmt}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">n/a</span>
                      )}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {anyWithin && (
        <p className="m-0 rounded-lg border border-warn-bd bg-warn-bg px-3 py-2 text-xs leading-relaxed text-warn">
          ≈ Several gains fall within the judge&apos;s ±{noiseFloor.toFixed(2)} noise floor — confirm them with paired,
          same-question deltas before calling them real.
        </p>
      )}
    </div>
  );
}

const rowMetricKeys: MetricKey[] = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"];

type RowSortKey =
  | "eval_id"
  | "category"
  | "split"
  | "abstained"
  | MetricKey
  | "elapsed_s";

function compareRowsNullsLast(a: number | null | undefined, b: number | null | undefined, sign: number) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return (a - b) * sign;
}

function RowsTriage({ tag, bands }: { tag: string; bands: EvalPolicy["quality_bands"] | undefined }) {
  const [filters, setFilters] = useState<RowFilters>(defaultRowFilters);
  const [sortKey, setSortKey] = useState<RowSortKey | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [selectedRow, setSelectedRow] = useState<EvalRow | null>(null);
  const rowsQuery = useQuery({
    queryKey: ["evalRows", tag],
    queryFn: () => getEvalRows(tag),
  });

  const rows = rowsQuery.data?.rows ?? [];
  const categories = useMemo(
    () => Array.from(new Set(rows.map((row) => row.category).filter(Boolean))).sort() as string[],
    [rows],
  );
  const splits = useMemo(
    () => Array.from(new Set(rows.map((row) => row.split).filter(Boolean))).sort() as string[],
    [rows],
  );
  const filteredRows = useMemo(() => filterRows(rows, filters), [rows, filters]);

  const toggleSort = (key: RowSortKey) => {
    if (sortKey !== key) {
      setSortKey(key);
      setSortDir(key === "eval_id" || key === "category" || key === "split" ? "asc" : "desc");
      return;
    }
    setSortDir((d) => (d === "asc" ? "desc" : "asc"));
  };

  const visibleRows = useMemo(() => {
    if (!sortKey) return filteredRows;
    const sign = sortDir === "asc" ? 1 : -1;
    return [...filteredRows].sort((a, b) => {
      if (sortKey === "eval_id") return sign * (a.eval_id ?? "").localeCompare(b.eval_id ?? "");
      if (sortKey === "category") return sign * (a.category ?? "").localeCompare(b.category ?? "");
      if (sortKey === "split") return sign * (a.split ?? "").localeCompare(b.split ?? "");
      if (sortKey === "abstained") return sign * (Number(a.abstained) - Number(b.abstained));
      if (sortKey === "elapsed_s") return compareRowsNullsLast(a.elapsed_s, b.elapsed_s, sign);
      return compareRowsNullsLast(a[sortKey], b[sortKey], sign);
    });
  }, [filteredRows, sortKey, sortDir]);

  if (rowsQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Per-question rows</CardTitle>
        </CardHeader>
        <CardContent>
          <p>Loading rows…</p>
        </CardContent>
      </Card>
    );
  }

  if (rowsQuery.error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Per-question rows</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">Failed to load rows.</p>
        </CardContent>
      </Card>
    );
  }

  if (rowsQuery.data?.holdout_redacted) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Per-question rows</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Holdout run — per-row data is redacted.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex-row items-baseline justify-between space-y-0">
        <div className="flex items-baseline gap-3">
          <CardTitle>Per-question rows</CardTitle>
          <span className="font-mono text-[11.5px] text-faint">{tag}</span>
        </div>
        <span className="font-mono text-[11.5px] text-muted-foreground">
          {visibleRows.length} of {rows.length} rows · click a column to sort
        </span>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative">
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/60"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
            <Input
              placeholder="Search question or eval_id"
              value={filters.search}
              onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
              className="w-[320px] pl-9"
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Category</span>
            <Select
              value={filters.category}
              onValueChange={(v) => setFilters((f) => ({ ...f, category: v || "all" }))}
            >
              <SelectTrigger className="w-[140px]" aria-label="Category">
                <SelectValue placeholder="Category" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">all</SelectItem>
                {categories.map((category) => (
                  <SelectItem key={category} value={category}>
                    {category}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Split</span>
            <Select
              value={filters.split}
              onValueChange={(v) => setFilters((f) => ({ ...f, split: v || "all" }))}
            >
              <SelectTrigger className="w-[140px]" aria-label="Split">
                <SelectValue placeholder="Split" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">all</SelectItem>
                {splits.map((split) => (
                  <SelectItem key={split} value={split}>
                    {split}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Button
              type="button"
              variant={filters.abstainedOnly ? "default" : "outline"}
              aria-pressed={filters.abstainedOnly}
              onClick={() => setFilters((f) => ({ ...f, abstainedOnly: !f.abstainedOnly }))}
            >
              Abstained
            </Button>
            <Button
              type="button"
              variant={filters.sourceMissOnly ? "default" : "outline"}
              aria-pressed={filters.sourceMissOnly}
              onClick={() => setFilters((f) => ({ ...f, sourceMissOnly: !f.sourceMissOnly }))}
            >
              Source miss
            </Button>
          </div>
        </div>

        <div className="max-h-[70vh] overflow-auto [&_[data-slot=table-container]]:overflow-visible">
          <Table className="border-separate border-spacing-0">
            <TableHeader>
              <TableRow>
                <SortableTableHead
                  label="eval_id"
                  fontSize={11}
                  uppercase
                  active={sortKey === "eval_id"}
                  dir={sortDir}
                  onClick={() => toggleSort("eval_id")}
                />
                <TableHead
                  className="sticky top-0 z-10 w-[300px] bg-muted text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground"
                >
                  Question
                </TableHead>
                <SortableTableHead
                  label="Category"
                  fontSize={11}
                  uppercase
                  active={sortKey === "category"}
                  dir={sortDir}
                  onClick={() => toggleSort("category")}
                />
                <SortableTableHead
                  label="Split"
                  fontSize={11}
                  uppercase
                  active={sortKey === "split"}
                  dir={sortDir}
                  onClick={() => toggleSort("split")}
                />
                <SortableTableHead
                  label="Abst."
                  fontSize={11}
                  uppercase
                  active={sortKey === "abstained"}
                  dir={sortDir}
                  onClick={() => toggleSort("abstained")}
                />
                {METRICS.map((m) => (
                  <SortableTableHead
                    key={m.key}
                    label={m.short}
                    fontSize={11}
                    uppercase
                    align="right"
                    active={sortKey === m.key}
                    dir={sortDir}
                    onClick={() => toggleSort(m.key)}
                  />
                ))}
                <SortableTableHead
                  label="Elapsed"
                  fontSize={11}
                  uppercase
                  align="right"
                  active={sortKey === "elapsed_s"}
                  dir={sortDir}
                  onClick={() => toggleSort("elapsed_s")}
                />
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibleRows.map((row, index) => {
                return (
                  <TableRow
                    key={row.eval_id ?? `${row.question}-${index}`}
                    role="button"
                    tabIndex={0}
                    className={cn("cursor-pointer", index % 2 === 1 && "bg-muted/40")}
                    onClick={() => setSelectedRow(row)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setSelectedRow(row);
                      }
                    }}
                  >
                    <TableCell className="align-top font-mono text-muted-foreground">
                      {row.eval_id ?? "—"}
                    </TableCell>
                    <TableCell className="w-[300px] max-w-[300px] align-top">
                      <span className="line-clamp-2">{row.question}</span>
                    </TableCell>
                    <TableCell className="align-top text-muted-foreground">
                      {row.category ?? "—"}
                    </TableCell>
                    <TableCell className="align-top text-muted-foreground">
                      {row.split ?? "—"}
                    </TableCell>
                    <TableCell className="align-top">
                      {row.abstained ? (
                        <span className="font-medium text-warn">
                          yes
                        </span>
                      ) : (
                        <span className="text-muted-foreground/60">—</span>
                      )}
                    </TableCell>
                    {rowMetricKeys.map((key) => {
                      const c = cell(row[key], bands);
                      return (
                        <TableCell
                          key={key}
                          className="text-right align-top font-mono"
                          style={{ color: c.bandKey === "na" ? "var(--faint)" : c.color }}
                        >
                          {c.fmt}
                        </TableCell>
                      );
                    })}
                    <TableCell className="text-right align-top font-mono text-muted-foreground">
                      {row.elapsed_s != null ? `${row.elapsed_s.toFixed(1)}s` : "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
              {visibleRows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} className="text-muted-foreground">
                    No rows match the current filters.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
      <EvalRowSheet row={selectedRow} onOpenChange={(open) => !open && setSelectedRow(null)} />
    </Card>
  );
}

export default function EvalDetail() {
  const { tag } = useParams();
  const enabled = Boolean(tag);
  const query = useQuery({
    queryKey: ["evalRun", tag],
    queryFn: () => getEvalRun(tag!),
    enabled,
    retry: false,
  });
  const policyQuery = useQuery({ queryKey: ["evalPolicy"], queryFn: getEvalPolicy });
  const policy = policyQuery.data ?? DEFAULT_EVAL_POLICY;

  if (!tag) return <p className="text-danger">Eval run not found.</p>;
  if (query.isLoading) return <p className="text-muted-foreground">Loading…</p>;
  if (query.error) return <p className="text-danger">Eval run not found.</p>;

  const run = query.data!;
  const holdout = Boolean(run.meta?.holdout);
  const ss = splitStyle(holdout);

  return (
    <div className="mx-auto flex max-w-[1240px] flex-col gap-4">
      <Link
        to="/evals"
        className="self-start text-[12.5px] font-medium text-muted-foreground hover:underline"
      >
        ← Back to all runs
      </Link>
      <div className="flex flex-col gap-2">
        <h1 className="m-0 font-mono text-[19px] font-semibold break-all">{run.tag}</h1>
        {run.label ? <div className="mt-1 font-serif text-lg text-muted-foreground">{run.label}</div> : null}
        <div className="flex flex-wrap gap-1.5">
          <span
            className="rounded-full bg-muted px-2.5 py-[3px] text-[11.5px] font-medium"
          >
            {run.model ?? "—"}
          </span>
          <span
            className="rounded-full px-2.5 py-0.5 text-xs font-medium"
            style={{ color: ss.color, background: ss.bg, border: `1px solid ${ss.bd}` }}
          >
            {holdout ? "Holdout" : "Regression + Dev"}
          </span>
          <Pill>{formatDate(run.date)}</Pill>
          <Pill mono>{run.git_sha ?? "—"}</Pill>
          <Pill>{run.question_count ?? "—"} questions</Pill>
        </div>
      </div>

      <SummaryTiles run={run} bands={policy.quality_bands} />
      <HowToRead noiseFloor={policy.noise_floor} />
      <div className="grid gap-4 lg:grid-cols-2 [&>*]:min-w-0">
        <ByCategory run={run} bands={policy.quality_bands} />
        <RunConfig run={run} />
      </div>
      <ComparePanel tag={tag} bands={policy.quality_bands} noiseFloor={policy.noise_floor} />
      <RowsTriage tag={tag} bands={policy.quality_bands} />
      <EvalRunLogs tag={tag} />
    </div>
  );
}
