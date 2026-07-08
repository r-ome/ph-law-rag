import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listEvalRuns } from "@/api/client";
import { fmtMetric } from "@/lib/metrics";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export default function Evals() {
  const query = useQuery({ queryKey: ["evalRuns"], queryFn: listEvalRuns });

  if (query.isLoading) return <p>Loading…</p>;
  if (query.error) return <p className="text-red-600">Failed to load eval runs.</p>;

  const runs = query.data?.runs ?? [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Evaluations</h1>
        <p className="text-sm text-muted-foreground">Read-only run metrics and comparisons.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Runs</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tag</TableHead>
                <TableHead>Date</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Label</TableHead>
                <TableHead className="text-right">Questions</TableHead>
                <TableHead className="text-right">Scored</TableHead>
                <TableHead className="text-right">Faithfulness</TableHead>
                <TableHead className="text-right">Relevancy</TableHead>
                <TableHead className="text-right">Precision</TableHead>
                <TableHead className="text-right">Recall</TableHead>
                <TableHead className="text-right">Abstention</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <TableRow key={run.tag}>
                  <TableCell className="max-w-[260px] break-all font-medium">
                    <Link to={`/evals/${encodeURIComponent(run.tag)}`} className="hover:underline">
                      {run.tag}
                    </Link>
                  </TableCell>
                  <TableCell>{run.date ?? "—"}</TableCell>
                  <TableCell>{run.model ?? "—"}</TableCell>
                  <TableCell>{run.label ?? "—"}</TableCell>
                  <TableCell className="text-right">{run.questions ?? "—"}</TableCell>
                  <TableCell className="text-right">{run.scored ?? "—"}</TableCell>
                  <TableCell className="text-right">{fmtMetric(run.faithfulness)}</TableCell>
                  <TableCell className="text-right">{fmtMetric(run.answer_relevancy)}</TableCell>
                  <TableCell className="text-right">{fmtMetric(run.context_precision)}</TableCell>
                  <TableCell className="text-right">{fmtMetric(run.context_recall)}</TableCell>
                  <TableCell className="text-right">{fmtMetric(run.abstention_accuracy)}</TableCell>
                </TableRow>
              ))}
              {runs.length === 0 && (
                <TableRow>
                  <TableCell colSpan={11} className="text-muted-foreground">
                    No eval runs found.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
