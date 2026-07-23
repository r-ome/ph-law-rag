import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getDocument, listChunks } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Panel, PanelBody, PanelHeader, PanelTitle } from "@/components/ui/panel";
import { docStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/format";

const MARKER_RE =
  /^((?:ART(?:ICLE)?|Art(?:icle)?|SEC(?:TION)?|Sec(?:tion)?|§)\.?\s*[\dA-Za-z().-]+\.?)(\s+)([\s\S]*)$/;

function splitIntoParagraphs(text: string): string[] {
  const blocks = text
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .filter(Boolean);
  const paragraphs: string[] = [];
  for (const block of blocks) {
    const lines = block
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    let current: string[] = [];
    for (const line of lines) {
      if (MARKER_RE.test(line) && current.length > 0) {
        paragraphs.push(current.join("\n"));
        current = [line];
      } else {
        current.push(line);
      }
    }
    if (current.length > 0) paragraphs.push(current.join("\n"));
  }
  return paragraphs;
}

function NormalizedText({ text }: { text: string }) {
  const paragraphs = splitIntoParagraphs(text);
  return (
    <div className="font-serif text-sm leading-[1.7]">
      {paragraphs.map((para, i) => {
        const m = para.match(MARKER_RE);
        return (
          <p key={i} className="mb-3.5 whitespace-pre-line last:mb-0">
            {m ? (
              <>
                <strong>{m[1]}</strong>
                {m[2]}
                {m[3]}
              </>
            ) : (
              para
            )}
          </p>
        );
      })}
    </div>
  );
}

function MetaRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "font-mono text-[12.5px]" : undefined}>{value}</dd>
    </>
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
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  if (error) return <p className="text-danger">Document not found.</p>;
  if (!data) return null;

  const hasEdges =
    data.amends.length > 0 ||
    data.repeals.length > 0 ||
    data.supersedes.length > 0 ||
    data.implements.length > 0;

  return (
    <div className="mx-auto max-w-[900px]">
      <Link
        to="/"
        className="text-[12.5px] text-muted-foreground hover:underline"
      >
        ← Corpus
      </Link>

      <h1 className="mt-4 font-serif text-[28px] font-semibold tracking-[-0.015em]">
        {data.title}
      </h1>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{data.category}</Badge>
        <Badge variant="secondary">{data.doc_type}</Badge>
        <Badge variant={docStatusTone(data.status)}>{data.status}</Badge>
        {data.official_number ? (
          <Badge variant="outline" className="font-mono">
            {data.official_number}
          </Badge>
        ) : null}
      </div>

      <dl className="mt-5 grid grid-cols-[170px_1fr] gap-x-4 gap-y-2.5 text-[13px]">
        <MetaRow
          label="Source URL"
          mono
          value={
            <a
              href={data.url}
              target="_blank"
              rel="noreferrer"
              className="hover:underline"
            >
              {data.url}
            </a>
          }
        />
        {data.source_index ? (
          <MetaRow label="Source index" value={data.source_index} />
        ) : null}
        {data.availability ? (
          <MetaRow label="Availability" value={data.availability} />
        ) : null}
        {data.structure ? <MetaRow label="Structure" value={data.structure} /> : null}
        {data.approval_date ? (
          <MetaRow label="Approval date" value={formatDate(data.approval_date)} />
        ) : null}
        {data.effectivity_date ? (
          <MetaRow label="Effectivity date" value={formatDate(data.effectivity_date)} />
        ) : null}
        {data.last_fetched ? (
          <MetaRow label="Last fetched" value={formatDate(data.last_fetched, { withTime: true })} />
        ) : null}
        {data.content_length != null ? (
          <MetaRow label="Content length" mono value={data.content_length} />
        ) : null}
        {data.extraction_method ? (
          <MetaRow label="Extraction method" value={data.extraction_method} />
        ) : null}
        {data.tags.length > 0 ? (
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
        ) : null}
      </dl>

      {hasEdges ? (
        <Panel className="mt-6">
          <PanelHeader>
            <PanelTitle className="text-[15px]">Amendment edges</PanelTitle>
          </PanelHeader>
          <PanelBody>
            <dl className="grid grid-cols-[170px_1fr] gap-x-4 gap-y-2.5 text-[13px]">
              {data.amends.length > 0 ? (
                <MetaRow label="Amends" mono value={data.amends.join(", ")} />
              ) : null}
              {data.repeals.length > 0 ? (
                <MetaRow label="Repeals" mono value={data.repeals.join(", ")} />
              ) : null}
              {data.supersedes.length > 0 ? (
                <MetaRow label="Supersedes" mono value={data.supersedes.join(", ")} />
              ) : null}
              {data.implements.length > 0 ? (
                <MetaRow label="Implements" mono value={data.implements.join(", ")} />
              ) : null}
            </dl>
          </PanelBody>
        </Panel>
      ) : null}

      <h2 className="mt-6 mb-2 text-[11px] font-semibold tracking-[0.06em] text-faint uppercase">
        Normalized text
      </h2>
      <ScrollArea className="h-[340px] rounded-xl border border-border bg-card">
        <div className="px-5 py-4">
          {data.normalized_text ? (
            <NormalizedText text={data.normalized_text} />
          ) : (
            <p className="text-muted-foreground">No normalized text.</p>
          )}
        </div>
      </ScrollArea>

      <div className="mt-3.5">
        <Button variant="outline" onClick={() => setShowChunks((v) => !v)}>
          {showChunks ? "Hide chunks" : "Show chunks"}
        </Button>
        {showChunks ? (
          <div className="mt-3 space-y-2">
            {chunksLoading ? (
              <p className="text-muted-foreground">Loading chunks…</p>
            ) : null}
            {chunkData ? (
              <>
                <p className="font-mono text-[12.5px] text-faint">
                  {chunkData.chunk_count} chunks
                </p>
                <ul className="space-y-2">
                  {chunkData.chunks.map((c) => (
                    <li
                      key={c.chunk_id}
                      className="rounded-xl border border-border bg-card p-3 text-sm"
                    >
                      <div className="mb-1 flex items-center gap-2 font-mono text-[11px] text-faint">
                        <span>#{c.chunk_index}</span>
                        <span>{c.char_count} chars</span>
                        <span>{c.token_estimate} tok</span>
                        {c.qdrant_id ? <span>{c.qdrant_id}</span> : null}
                      </div>
                      <p>{c.text.slice(0, 200)}</p>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
