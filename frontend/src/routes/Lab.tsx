import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { inspectRetrieval } from "@/api/client";
import TraceView from "@/components/TraceView";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

type Strategy = "auto" | "default" | "current_law";

export default function Lab() {
  const [question, setQuestion] = useState("");
  const [strategy, setStrategy] = useState<Strategy>("auto");

  const mutation = useMutation({
    mutationFn: () =>
      inspectRetrieval({
        question: question.trim(),
        strategy: strategy === "auto" ? null : strategy,
      }),
  });

  function run() {
    if (!question.trim() || mutation.isPending) return;
    mutation.mutate();
  }

  const data = mutation.data;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Retrieval Lab</h1>
        <p className="text-sm text-muted-foreground">Run one query and inspect the retrieval trace.</p>
      </div>

      <Card>
        <CardContent className="space-y-3">
          <Textarea
            value={question}
            disabled={mutation.isPending}
            placeholder="Ask a legal question..."
            className="min-h-28 resize-none"
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Select value={strategy} onValueChange={(v) => setStrategy((v ?? "auto") as Strategy)}>
              <SelectTrigger className="w-[180px]" aria-label="Strategy">
                <SelectValue placeholder="Strategy" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto (router)</SelectItem>
                <SelectItem value="default">default</SelectItem>
                <SelectItem value="current_law">current_law</SelectItem>
              </SelectContent>
            </Select>
            <Button disabled={mutation.isPending || !question.trim()} onClick={run}>
              {mutation.isPending ? "Running..." : "Run"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {mutation.isError && <p className="text-sm text-red-600">Failed to run retrieval.</p>}
      {data?.error && (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-red-700">
          {data.error_message ?? "Retrieval returned an error."}
        </p>
      )}
      {data && !data.error && (
        <Card>
          <CardHeader>
            <CardTitle>Answer</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="whitespace-pre-wrap text-sm leading-6">{data.answer}</div>
            {data.sources.length > 0 && (
              <div className="grid gap-2 md:grid-cols-2">
                {data.sources.map((source) => (
                  <div key={source.ref} className="rounded-md border p-3 text-sm">
                    <div className="font-medium">
                      [{source.ref}] {source.title}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-2">
                      <Badge variant="secondary">{source.source_id}</Badge>
                      {source.locator && <Badge variant="outline">{source.locator}</Badge>}
                    </div>
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2 block break-all text-xs hover:underline"
                    >
                      {source.url}
                    </a>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}
      {data?.trace && <TraceView trace={data.trace} />}
      {data && !data.trace && (
        <p className="text-sm text-muted-foreground">No trace was returned for this run.</p>
      )}
    </div>
  );
}
