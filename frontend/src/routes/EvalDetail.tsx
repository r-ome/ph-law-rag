import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getEvalDiff, getEvalRows, getEvalRun, listEvalRuns } from "@/api/client";
import type { EvalDiff, EvalRunDetail } from "@/api/client";
import { deltaClass, fmtDelta, fmtMetric } from "@/lib/metrics";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
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

const metricKeys = [
  ["faithfulness", "Faithfulness"],
  ["answer_relevancy", "Relevancy"],
  ["context_precision", "Precision"],
  ["context_recall", "Recall"],
] as const;

function MetricCard({ label, value, note }: { label: string; value: number | null | undefined; note?: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl">{fmtMetric(value)}</CardTitle>
      </CardHeader>
      {note && <CardContent className="text-sm text-muted-foreground">{note}</CardContent>}
    </Card>
  );
}

function SummaryCards({ run }: { run: EvalRunDetail }) {
  const overall = run.summary?.overall;
  const abstention = run.summary?.abstention;
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
      {metricKeys.map(([key, label]) => (
        <MetricCard key={key} label={label} value={overall?.[key]} />
      ))}
      {abstention ? (
        <MetricCard
          label="Abstention"
          value={abstention.accuracy}
          note={`${abstention.correct ?? "—"} / ${abstention.total ?? "—"} correct`}
        />
      ) : (
        <MetricCard label="Abstention" value={undefined} />
      )}
    </div>
  );
}

function ByCategory({ run }: { run: EvalRunDetail }) {
  const categories = Object.entries(run.summary?.by_category ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return (
    <Card>
      <CardHeader>
        <CardTitle>By Category</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Category</TableHead>
              <TableHead className="text-right">n</TableHead>
              {metricKeys.map(([, label]) => (
                <TableHead key={label} className="text-right">
                  {label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {categories.map(([category, metrics]) => (
              <TableRow key={category}>
                <TableCell>{category}</TableCell>
                <TableCell className="text-right">{metrics.n ?? "—"}</TableCell>
                {metricKeys.map(([key]) => (
                  <TableCell key={key} className="text-right">
                    {fmtMetric(metrics[key])}
                  </TableCell>
                ))}
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

function DeltaCell({ value }: { value: number | null | undefined }) {
  return <span className={deltaClass(value)}>{fmtDelta(value)}</span>;
}

function ComparePanel({ tag }: { tag: string }) {
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
        <CardTitle>Compare</CardTitle>
        <CardDescription>Candidate run against a baseline.</CardDescription>
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
                  {run.tag}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <p className="text-sm text-muted-foreground">No other runs available for comparison.</p>
        )}
        {diffQuery.isLoading && <p>Loading comparison…</p>}
        {diffQuery.error && <p className="text-sm text-red-600">Failed to load comparison.</p>}
        {diff && <DiffTables diff={diff} />}
      </CardContent>
    </Card>
  );
}

function DiffTables({ diff }: { diff: EvalDiff }) {
  const cats = Object.entries(diff.by_category).sort(([a], [b]) => a.localeCompare(b));
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Metric</TableHead>
              <TableHead className="text-right">Candidate</TableHead>
              <TableHead className="text-right">Baseline</TableHead>
              <TableHead className="text-right">Δ</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {metricKeys.map(([key, label]) => (
              <TableRow key={key}>
                <TableCell>{label}</TableCell>
                <TableCell className="text-right">{fmtMetric(diff.overall.candidate[key])}</TableCell>
                <TableCell className="text-right">{fmtMetric(diff.overall.baseline[key])}</TableCell>
                <TableCell className="text-right">
                  <DeltaCell value={diff.overall.delta[key]} />
                </TableCell>
              </TableRow>
            ))}
            <TableRow>
              <TableCell>Abstention</TableCell>
              <TableCell className="text-right">{fmtMetric(diff.abstention.candidate)}</TableCell>
              <TableCell className="text-right">{fmtMetric(diff.abstention.baseline)}</TableCell>
              <TableCell className="text-right">
                <DeltaCell value={diff.abstention.delta} />
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Category</TableHead>
              <TableHead>Status</TableHead>
              {metricKeys.map(([, label]) => (
                <TableHead key={label} className="text-right">
                  {label} Δ
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
                {metricKeys.map(([key]) => (
                  <TableCell key={key} className="text-right">
                    {row.delta ? <DeltaCell value={row.delta[key]} /> : "n/a"}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function RowsDrilldown({ tag }: { tag: string }) {
  const [showRows, setShowRows] = useState(false);
  const rowsQuery = useQuery({
    queryKey: ["evalRows", tag],
    queryFn: () => getEvalRows(tag),
    enabled: showRows,
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Rows</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Button onClick={() => setShowRows((value) => !value)}>
          {showRows ? "Hide rows" : "Show rows"}
        </Button>
        {rowsQuery.isLoading && <p>Loading rows…</p>}
        {rowsQuery.error && <p className="text-sm text-red-600">Failed to load rows.</p>}
        {showRows && rowsQuery.data && (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Question</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Abstained</TableHead>
                  {metricKeys.map(([, label]) => (
                    <TableHead key={label} className="text-right">
                      {label}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rowsQuery.data.rows.map((row, index) => (
                  <TableRow key={row.eval_id ?? `${row.question}-${index}`}>
                    <TableCell className="min-w-[320px] align-top">
                      <details>
                        <summary className="cursor-pointer font-medium">{row.question}</summary>
                        <div className="mt-3 space-y-3 text-sm">
                          <div>
                            <div className="text-muted-foreground">Answer</div>
                            <div className="whitespace-pre-wrap">{row.answer || "—"}</div>
                          </div>
                          <div>
                            <div className="text-muted-foreground">Ground truth</div>
                            <div className="whitespace-pre-wrap">{row.ground_truth ?? "—"}</div>
                          </div>
                          <div>
                            <div className="text-muted-foreground">Contexts</div>
                            <ScrollArea className="mt-1 h-36 rounded-md border p-3">
                              <div className="space-y-3 whitespace-pre-wrap">
                                {row.contexts.length > 0
                                  ? row.contexts.map((context, contextIndex) => (
                                      <div key={contextIndex}>{context}</div>
                                    ))
                                  : "—"}
                              </div>
                            </ScrollArea>
                          </div>
                        </div>
                      </details>
                    </TableCell>
                    <TableCell className="align-top">{row.category ?? "—"}</TableCell>
                    <TableCell className="align-top">
                      <Badge variant={row.abstained ? "secondary" : "outline"}>
                        {row.abstained ? "yes" : "no"}
                      </Badge>
                    </TableCell>
                    {metricKeys.map(([key]) => (
                      <TableCell key={key} className="text-right align-top">
                        {fmtMetric(row[key])}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
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

  if (!tag) return <p className="text-red-600">Eval run not found.</p>;
  if (query.isLoading) return <p>Loading…</p>;
  if (query.error) return <p className="text-red-600">Eval run not found.</p>;

  const run = query.data!;

  return (
    <div className="space-y-5">
      <Link to="/evals" className="text-sm hover:underline">
        Back to evals
      </Link>
      <div className="space-y-3">
        <h1 className="break-all text-2xl font-semibold">{run.tag}</h1>
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{run.model ?? "model n/a"}</Badge>
          <Badge variant="outline">{run.label || "label n/a"}</Badge>
          <Badge variant="outline">{run.date ?? "date n/a"}</Badge>
          <Badge variant="outline">{run.git_sha ?? "git n/a"}</Badge>
          <Badge>{run.question_count ?? "—"} questions</Badge>
          <Badge>{run.scored_count ?? "—"} scored</Badge>
        </div>
      </div>
      <SummaryCards run={run} />
      <ByCategory run={run} />
      <ComparePanel tag={tag} />
      <RowsDrilldown tag={tag} />
    </div>
  );
}
