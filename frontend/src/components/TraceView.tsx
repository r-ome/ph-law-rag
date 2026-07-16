import type { ChunkTrace, TraceRecord } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { ChevronDownIcon, ChevronUpIcon } from "lucide-react";
import { useState } from "react";

function textValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "none";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function score(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(4) : "n/a";
}

function ChunkCard({ chunk }: { chunk: ChunkTrace }) {
  const [expanded, setExpanded] = useState(false);
  const fullText = chunk.text || chunk.preview;
  const canExpand = fullText.length > chunk.preview.length;
  const visibleText = expanded ? fullText : chunk.preview;

  return (
    <div className="rounded-md border bg-background p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{score(chunk.score)}</Badge>
        <span className="font-medium">{chunk.source_id || "unknown"}</span>
        {chunk.unit_label && <span className="text-muted-foreground">{chunk.unit_label}</span>}
        {chunk.expanded_from_parent && <Badge>expanded_from_parent</Badge>}
        {chunk.expanded_from_sibling && (
          <Badge>
            expanded_from_sibling {chunk.sibling_offset != null ? `(${chunk.sibling_offset})` : ""}
          </Badge>
        )}
        {chunk.sibling_seed_chunk_id && (
          <Badge variant="outline">seed {chunk.sibling_seed_chunk_id}</Badge>
        )}
        {chunk.dedup_merged_chunk_ids.length > 0 && (
          <Badge variant="outline">dedup {chunk.dedup_merged_chunk_ids.length}</Badge>
        )}
      </div>
      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono text-xs leading-5">
        {visibleText}
      </pre>
      {canExpand && (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="mt-2"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? (
            <ChevronUpIcon data-icon="inline-start" />
          ) : (
            <ChevronDownIcon data-icon="inline-start" />
          )}
          {expanded ? "Show less" : "Read more"}
        </Button>
      )}
    </div>
  );
}

function ChunkList({ title, chunks }: { title: string; chunks: ChunkTrace[] }) {
  return (
    <div className="min-w-0 space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {chunks.length === 0 ? (
        <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">none</p>
      ) : (
        chunks.map((chunk) => (
          <ChunkCard key={chunk.chunk_id} chunk={chunk} />
        ))
      )}
    </div>
  );
}

export default function TraceView({ trace }: { trace: TraceRecord }) {
  const strategy = textValue(trace.retrieval_strategy.strategy);
  const router = trace.intent_router;
  const flags = Object.entries(trace.feature_flags);
  const counts = trace.stage_counts;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Trace</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap gap-2 text-sm">
          <Badge>{strategy}</Badge>
          <Badge variant="outline">{trace.latency_ms ?? "n/a"} ms</Badge>
          <Badge variant="outline">prompt {trace.prompt_length ?? 0}</Badge>
          <Badge variant="secondary">{trace.generator_model ?? "no generator"}</Badge>
          {trace.abstained && <Badge variant="outline">abstained</Badge>}
          {trace.error && <Badge variant="destructive">error</Badge>}
          <Badge variant="outline">retrieved {counts.retrieved ?? 0}</Badge>
          <Badge variant="outline">reranked {counts.pre_expansion ?? 0}</Badge>
          <Badge variant="outline">selected {counts.selected ?? 0}</Badge>
        </div>

        <Separator />

        <section className="grid gap-2 text-sm md:grid-cols-4">
          <h2 className="font-semibold md:col-span-4">Router</h2>
          <div>
            <span className="text-muted-foreground">Enabled</span>
            <div>{textValue(router.enabled)}</div>
          </div>
          <div>
            <span className="text-muted-foreground">Model</span>
            <div>{textValue(router.model)}</div>
          </div>
          <div>
            <span className="text-muted-foreground">Decision</span>
            <div className="break-all">{textValue(router.decision)}</div>
          </div>
          <div>
            <span className="text-muted-foreground">Skipped</span>
            <div>{textValue(router.skipped_reason)}</div>
          </div>
        </section>

        <Separator />

        <section className="grid gap-4 xl:grid-cols-3">
          <ChunkList title="Retrieved" chunks={trace.retrieved_chunks} />
          <ChunkList title="Reranked" chunks={trace.pre_expansion_chunks} />
          <ChunkList title="Selected" chunks={trace.selected_chunks} />
        </section>

        <Separator />

        <section className="space-y-2">
          <h2 className="text-sm font-semibold">Feature Flags</h2>
          {flags.length === 0 ? (
            <p className="text-sm text-muted-foreground">none</p>
          ) : (
            <div className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
              {flags.map(([key, value]) => (
                <div key={key} className="rounded-md border p-2">
                  <div className="text-muted-foreground">{key}</div>
                  <div className="break-all font-medium">{textValue(value)}</div>
                </div>
              ))}
            </div>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
