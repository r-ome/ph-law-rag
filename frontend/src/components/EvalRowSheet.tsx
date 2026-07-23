import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { lookupChunks } from "@/api/client";
import type { ChunkLookupHit, EvalRow } from "@/api/client";
import { verdictBadgeVariant } from "@/lib/evalRows";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ChevronDownIcon, ChevronUpIcon } from "lucide-react";

const CHUNK_LOOKUP_LIMIT = 64;

function SourceChips({
  sources,
  missing,
}: {
  sources: string[];
  missing: string[];
}) {
  const missingSet = new Set(missing);
  const deduped = Array.from(new Set(sources));
  if (deduped.length === 0) {
    return <p className="text-sm text-muted-foreground">none</p>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {deduped.map((source) => (
        <Badge key={source} variant={missingSet.has(source) ? "destructive" : "secondary"} className="font-mono">
          {source}
        </Badge>
      ))}
    </div>
  );
}

function ChunkHit({ hit }: { hit: ChunkLookupHit }) {
  const [expanded, setExpanded] = useState(false);
  const preview = hit.text.slice(0, 240);
  const canExpand = hit.text.length > preview.length;
  return (
    <div className="rounded-md border bg-background p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Link to={`/documents/${hit.doc_id}`} className="font-medium text-primary hover:underline">
          {hit.title ?? hit.doc_id}
        </Link>
        <span className="text-muted-foreground">#{hit.chunk_index ?? "—"}</span>
        <span className="text-muted-foreground">{hit.char_count ?? hit.text.length} chars</span>
      </div>
      <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono text-xs leading-5">
        {expanded ? hit.text : preview}
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
          {expanded ? <ChevronUpIcon data-icon="inline-start" /> : <ChevronDownIcon data-icon="inline-start" />}
          {expanded ? "Show less" : "Read more"}
        </Button>
      )}
    </div>
  );
}

function PipelineStages({ row }: { row: EvalRow }) {
  if (row.debug_stages.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">Pipeline stages</h3>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Stage</TableHead>
              <TableHead>in → out</TableHead>
              <TableHead className="text-right">ms</TableHead>
              <TableHead>Fired</TableHead>
              <TableHead>Model / prompt</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {row.debug_stages.map((stage, index) => {
              const hasModelOrPrompt = stage.model != null || stage.prompt_length != null;
              const model = stage.model?.trim() ? stage.model : "—";
              return (
                <TableRow key={`${stage.name}-${index}`}>
                  <TableCell>{stage.name}</TableCell>
                  <TableCell>
                    {stage.in_n ?? "—"} → {stage.out_n ?? "—"}
                  </TableCell>
                  <TableCell className="text-right">
                    {stage.ms != null ? stage.ms.toFixed(1) : "—"}
                  </TableCell>
                  <TableCell>{stage.fired === true ? "✓" : stage.fired === false ? "✗" : "—"}</TableCell>
                  <TableCell>
                    {hasModelOrPrompt
                      ? `${model}${stage.prompt_length != null ? ` (${stage.prompt_length})` : ""}`
                      : "—"}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function SelectedChunks({ row, open }: { row: EvalRow; open: boolean }) {
  const chunkIds = row.selected_chunk_ids;
  const lookupChunkIds = chunkIds.slice(0, CHUNK_LOOKUP_LIMIT);
  const chunkQuery = useQuery({
    queryKey: ["chunkLookup", row.eval_id, lookupChunkIds],
    queryFn: () => lookupChunks(lookupChunkIds),
    enabled: open && lookupChunkIds.length > 0,
  });

  if (chunkIds.length === 0) return null;

  return (
    <section className="space-y-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">Selected chunks</h3>
      {chunkIds.length > lookupChunkIds.length && (
        <p className="text-sm text-muted-foreground">
          Showing first {lookupChunkIds.length} of {chunkIds.length} selected chunks.
        </p>
      )}
      {chunkQuery.isLoading && <p className="text-sm text-muted-foreground">Loading chunks…</p>}
      {chunkQuery.error && <p className="text-sm text-danger">Failed to load chunks.</p>}
      {chunkQuery.data && (
        <div className="space-y-2">
          {chunkQuery.data.chunks.map((hit) => (
            <ChunkHit key={hit.chunk_id} hit={hit} />
          ))}
          {chunkQuery.data.missing.length > 0 && (
            <p className="text-sm text-muted-foreground">
              Stale chunk IDs (re-indexed since this run): {chunkQuery.data.missing.join(", ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export default function EvalRowSheet({
  row,
  onOpenChange,
}: {
  row: EvalRow | null;
  onOpenChange: (open: boolean) => void;
}) {
  const open = row != null;
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-2xl!">
        {row && (
          <div className="flex h-full flex-col overflow-hidden">
            <SheetHeader>
              <SheetTitle className="break-all">{row.eval_id ?? "row"}</SheetTitle>
            </SheetHeader>
            <ScrollArea className="min-h-0 flex-1 px-4 pb-6">
              <div className="space-y-5">
                <div className="space-y-2">
                  <p className="font-serif text-[16.5px] font-medium leading-[1.45]">{row.question}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {row.category && <Badge variant="outline">{row.category}</Badge>}
                    {row.split && <Badge variant="outline">{row.split}</Badge>}
                    {row.topic && <Badge variant="outline">{row.topic}</Badge>}
                    {row.facet && <Badge variant="outline">{row.facet}</Badge>}
                    <Badge variant={row.abstained ? "secondary" : "outline"}>
                      {row.abstained ? "abstained" : "answered"}
                    </Badge>
                    {row.profile && <Badge variant="outline">{row.profile}</Badge>}
                    {row.model_choice?.model && (
                      <span className="inline-flex items-center gap-1.5">
                        <Badge>{row.model_choice.model}</Badge>
                        {row.model_choice.reason && (
                          <span className="text-xs text-muted-foreground">
                            {row.model_choice.reason}
                          </span>
                        )}
                      </span>
                    )}
                    {row.corrective_retrieval?.fired && (
                      <Badge variant="secondary">
                        corrective +{row.corrective_retrieval.added_chunks ?? 0}
                      </Badge>
                    )}
                  </div>
                </div>

                {row.evidence && (
                  <section className="space-y-2">
                    <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">Evidence</h3>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={verdictBadgeVariant(row.evidence.verdict)}>
                        {row.evidence.verdict ?? "—"}
                      </Badge>
                      {row.evidence.method && (
                        <span className="text-sm text-muted-foreground">{row.evidence.method}</span>
                      )}
                    </div>
                    {row.evidence.missing_facets.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {row.evidence.missing_facets.map((facet) => (
                          <Badge key={facet} variant="outline" className="border-destructive text-destructive">
                            {facet}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </section>
                )}

                <section className="space-y-3">
                    <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">Sources</h3>
                  <div className="space-y-2">
                    <div>
                      <div className="text-[11px] text-faint">Expected</div>
                      <SourceChips sources={row.expected_sources} missing={row.expected_missing} />
                    </div>
                    <div>
                      <div className="text-[11px] text-faint">Retrieved</div>
                      <SourceChips sources={row.retrieved_sources} missing={[]} />
                    </div>
                    <div>
                      <div className="text-[11px] text-faint">Cited</div>
                      <SourceChips sources={row.cited_sources} missing={[]} />
                    </div>
                  </div>
                </section>

                <PipelineStages row={row} />

                <section className="space-y-3">
                  <div>
                    <div className="text-[11px] text-faint">Answer</div>
                    <div className="whitespace-pre-wrap font-serif text-sm leading-[1.6]">{row.answer || "—"}</div>
                  </div>
                  <div>
                    <div className="text-[11px] text-faint">Ground truth</div>
                    <div className="whitespace-pre-wrap text-sm text-muted-foreground">
                      {row.ground_truth ?? "—"}
                    </div>
                  </div>
                </section>

                <section className="space-y-2">
                  <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">Contexts</h3>
                  <ScrollArea className="h-40 rounded-md border p-3">
                    <div className="space-y-3 whitespace-pre-wrap text-sm">
                      {row.contexts.length > 0
                        ? row.contexts.map((context, index) => (
                            <div key={index}>
                              <span className="text-muted-foreground">[{index + 1}]</span> {context}
                            </div>
                          ))
                        : "—"}
                    </div>
                  </ScrollArea>
                </section>

                <SelectedChunks row={row} open={open} />
              </div>
            </ScrollArea>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
