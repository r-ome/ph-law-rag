import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getDocument, listChunks } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

function statusVariant(status: string): "default" | "secondary" | "outline" {
  if (status === "operative") return "default";
  if (status === "unknown") return "outline";
  return "secondary";
}

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[160px_1fr] gap-2 py-1">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export default function CorpusDetail() {
  const { docId } = useParams<{ docId: string }>();
  const [showChunks, setShowChunks] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["document", docId],
    queryFn: () => getDocument(docId!),
    enabled: Boolean(docId),
  });

  const { data: chunkData, isLoading: chunksLoading } = useQuery({
    queryKey: ["chunks", docId],
    queryFn: () => listChunks(docId!),
    enabled: Boolean(docId) && showChunks,
  });

  if (!docId) return <p>Not found.</p>;
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p className="text-red-600">Document not found.</p>;
  if (!data) return null;

  const hasEdges =
    data.amends.length > 0 ||
    data.repeals.length > 0 ||
    data.supersedes.length > 0 ||
    data.implements.length > 0;

  return (
    <div className="space-y-6">
      <Link to="/" className="text-sm text-muted-foreground hover:underline">
        ← Corpus
      </Link>

      <div>
        <h1 className="text-2xl font-semibold">{data.title}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{data.category}</Badge>
          <Badge variant="secondary">{data.doc_type}</Badge>
          <Badge variant={statusVariant(data.status)}>{data.status}</Badge>
          {data.official_number && <Badge variant="outline">{data.official_number}</Badge>}
        </div>
      </div>

      <dl className="text-sm">
        <MetaRow
          label="Source URL"
          value={
            <a href={data.url} target="_blank" rel="noreferrer" className="hover:underline">
              {data.url}
            </a>
          }
        />
        {data.source_index && <MetaRow label="Source index" value={data.source_index} />}
        {data.availability && <MetaRow label="Availability" value={data.availability} />}
        {data.structure && <MetaRow label="Structure" value={data.structure} />}
        {data.approval_date && <MetaRow label="Approval date" value={data.approval_date} />}
        {data.effectivity_date && (
          <MetaRow label="Effectivity date" value={data.effectivity_date} />
        )}
        {data.last_fetched && <MetaRow label="Last fetched" value={data.last_fetched} />}
        {data.content_length != null && (
          <MetaRow label="Content length" value={data.content_length} />
        )}
        {data.extraction_method && (
          <MetaRow label="Extraction method" value={data.extraction_method} />
        )}
        {data.tags.length > 0 && (
          <MetaRow
            label="Tags"
            value={
              <div className="flex flex-wrap gap-1">
                {data.tags.map((t) => (
                  <Badge key={t} variant="outline">
                    {t}
                  </Badge>
                ))}
              </div>
            }
          />
        )}
      </dl>

      {hasEdges && (
        <div>
          <h2 className="text-sm font-semibold text-muted-foreground">Amendment edges</h2>
          <dl className="text-sm">
            {data.amends.length > 0 && (
              <MetaRow label="Amends" value={data.amends.join(", ")} />
            )}
            {data.repeals.length > 0 && (
              <MetaRow label="Repeals" value={data.repeals.join(", ")} />
            )}
            {data.supersedes.length > 0 && (
              <MetaRow label="Supersedes" value={data.supersedes.join(", ")} />
            )}
            {data.implements.length > 0 && (
              <MetaRow label="Implements" value={data.implements.join(", ")} />
            )}
          </dl>
        </div>
      )}

      <div>
        <h2 className="mb-2 text-sm font-semibold text-muted-foreground">Normalized text</h2>
        <ScrollArea className="h-[50vh] rounded-md border p-4">
          {data.normalized_text ? (
            <pre className="whitespace-pre-wrap font-sans text-sm">{data.normalized_text}</pre>
          ) : (
            <p className="text-muted-foreground">No normalized text.</p>
          )}
        </ScrollArea>
      </div>

      <div>
        <Button variant="outline" onClick={() => setShowChunks((v) => !v)}>
          {showChunks ? "Hide chunks" : "Show chunks"}
        </Button>
        {showChunks && (
          <div className="mt-3 space-y-2">
            {chunksLoading && <p>Loading chunks…</p>}
            {chunkData && (
              <>
                <p className="text-sm text-muted-foreground">
                  {chunkData.chunk_count} chunks
                </p>
                <ul className="space-y-2">
                  {chunkData.chunks.map((c) => (
                    <li key={c.chunk_id} className="rounded-md border p-3 text-sm">
                      <div className="mb-1 flex items-center gap-2 text-muted-foreground">
                        <span>#{c.chunk_index}</span>
                        <span>{c.char_count} chars</span>
                        <span>{c.token_estimate} tok</span>
                        {c.qdrant_id && <span className="font-mono">{c.qdrant_id}</span>}
                      </div>
                      <p>{c.text.slice(0, 200)}</p>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
