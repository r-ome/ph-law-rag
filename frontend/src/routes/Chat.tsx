import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ask,
  getConversation,
  listConversations,
  type ChatSource,
  type ConversationTurn,
} from "@/api/client";
import { citedRefs, renderAnswerWithCitations } from "@/lib/citations";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type Highlight = { turnIndex: number; ref: number } | null;
type SourceGroup = ChatSource & { refs: number[] };

function sourceKey(source: ChatSource) {
  return [
    source.source_id,
    source.title,
    source.url,
    source.locator ?? "",
    source.via ?? "",
  ].join("");
}

function groupSources(sources: ChatSource[]): SourceGroup[] {
  const groups = new Map<string, SourceGroup>();
  for (const source of sources) {
    const key = sourceKey(source);
    const existing = groups.get(key);
    if (existing) {
      existing.refs.push(source.ref);
    } else {
      groups.set(key, { ...source, refs: [source.ref] });
    }
  }
  return [...groups.values()];
}

function SourceCards({
  turnIndex,
  answer,
  sources,
  highlight,
}: {
  turnIndex: number;
  answer: string;
  sources: ChatSource[];
  highlight: Highlight;
}) {
  if (sources.length === 0) return null;
  const refs = citedRefs(answer);
  const sourceGroups = groupSources(sources.filter((source) => refs.has(source.ref)));
  if (sourceGroups.length === 0) return null;

  return (
    <div className="mt-4 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
      {sourceGroups.map((source) => {
        const active =
          highlight != null &&
          highlight.turnIndex === turnIndex &&
          source.refs.includes(highlight.ref);
        const refLabel = source.refs.join(", ");
        return (
          <div
            key={`${turnIndex}-${sourceKey(source)}`}
            id={`src-${turnIndex}-${source.refs[0]}`}
            className={cn(
              "rounded-[9px] border border-border bg-muted p-3 transition-shadow",
              active && "ring-2 ring-primary"
            )}
          >
            {source.refs.slice(1).map((ref) => (
              <span key={ref} id={`src-${turnIndex}-${ref}`} className="sr-only" />
            ))}
            <div className="text-[12.5px] leading-[1.35] font-semibold">
              <span className="font-mono text-primary">[{refLabel}]</span> {source.title}
            </div>
            {source.locator ? (
              <div className="mt-1 text-[11.5px] text-muted-foreground">
                {source.locator}
              </div>
            ) : null}
            <div className="mt-2 flex items-center gap-1.5">
              {source.via ? (
                <span className="rounded-full bg-primary-bg px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  {source.via}
                </span>
              ) : null}
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="truncate font-mono text-[10.5px] text-faint hover:underline"
              >
                {source.url}
              </a>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function TurnView({
  turn,
  onCite,
  highlight,
}: {
  turn: ConversationTurn;
  onCite: (turnIndex: number, ref: number) => void;
  highlight: Highlight;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="max-w-[80%] self-end rounded-[14px_14px_4px_14px] bg-primary px-[15px] py-2.5 text-[13.5px] leading-[1.5] text-primary-foreground">
        {turn.question}
      </div>
      <div className="rounded-[14px_14px_14px_4px] border border-border bg-card px-[18px] py-4 shadow-[var(--shadow)]">
        <div className="mb-3 flex items-center gap-2">
          <span className="flex h-[22px] w-[22px] items-center justify-center rounded-[6px] border border-primary-bd bg-primary-bg font-serif text-[12px] font-bold text-primary">
            §
          </span>
          <span className="text-[11px] font-semibold tracking-[0.06em] text-faint uppercase">
            Answer
          </span>
        </div>
        <div className="font-serif text-[15px] leading-[1.65] whitespace-pre-wrap">
          {renderAnswerWithCitations(turn.answer, turn.sources, (ref) =>
            onCite(turn.turn_index, ref),
          )}
        </div>
        <SourceCards
          turnIndex={turn.turn_index}
          answer={turn.answer}
          sources={turn.sources}
          highlight={highlight}
        />
      </div>
    </div>
  );
}

export default function Chat() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<Highlight>(null);
  const latestTurnRef = useRef<HTMLDivElement>(null);

  const conversationsQuery = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
  });

  const conversationQuery = useQuery({
    queryKey: ["conversation", sessionId],
    queryFn: () => getConversation(sessionId!),
    enabled: Boolean(sessionId),
  });

  const turns = useMemo(
    () => conversationQuery.data?.turns ?? [],
    [conversationQuery.data],
  );

  useEffect(() => {
    if (turns.length === 0 && !pendingQuestion) return;
    latestTurnRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, pendingQuestion]);

  const mutation = useMutation({
    mutationFn: (q: string) => ask({ question: q, session_id: sessionId ?? null }),
    onMutate: (q) => {
      setPendingQuestion(q);
      setQuestion("");
    },
    onSuccess: (res) => {
      setPendingQuestion(null);
      if (!sessionId) navigate(`/chat/${res.session_id}`);
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["conversation", res.session_id] });
    },
    onError: () => {
      if (pendingQuestion) setQuestion(pendingQuestion);
      setPendingQuestion(null);
    },
  });

  function onCite(turnIndex: number, ref: number) {
    setHighlight({ turnIndex, ref });
    const el = document.getElementById(`src-${turnIndex}-${ref}`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => setHighlight(null), 1400);
  }

  function send() {
    const q = question.trim();
    if (!q || mutation.isPending) return;
    mutation.mutate(q);
  }

  return (
    <div className="grid h-[calc(100vh-7rem)] min-h-0 grid-cols-1 overflow-hidden rounded-xl border border-border lg:grid-cols-[260px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col overflow-y-auto border-r border-border bg-muted">
        <div className="flex items-center justify-between px-4 pt-4 pb-2.5">
          <h2 className="font-serif text-[15px] font-semibold">Conversations</h2>
          <Button
            size="xs"
            variant="tint-primary-pill"
            nativeButton={false}
            render={<Link to="/chat" />}
          >
            New
          </Button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-0.5 px-2.5 pb-3.5">
          {conversationsQuery.isLoading ? (
            <p className="p-2 text-sm text-muted-foreground">Loading…</p>
          ) : null}
          {conversationsQuery.error ? (
            <p className="p-2 text-sm text-danger">Failed to load conversations.</p>
          ) : null}
          {(conversationsQuery.data?.conversations ?? []).map((c) => (
            <Link
              key={c.session_id}
              to={`/chat/${c.session_id}`}
              className={cn(
                "rounded-lg px-3 py-2.5 hover:bg-card",
                c.session_id === sessionId && "border border-border bg-card",
              )}
            >
              <span className="block truncate text-[12.5px] font-medium">{c.title}</span>
              <span className="mt-0.5 block text-[11px] text-faint">
                {c.turn_count} turns
              </span>
            </Link>
          ))}
        </div>
      </aside>

      <section className="flex min-h-0 flex-col overflow-hidden">
        <div className="min-h-0 flex-1 overflow-y-auto px-[30px] py-[26px]">
          <div className="mx-auto flex max-w-[760px] flex-col gap-[26px]">
            {!sessionId && turns.length === 0 && !pendingQuestion ? (
              <div className="flex h-full items-center justify-center py-20 text-center text-muted-foreground">
                Ask a question about Philippine law.
              </div>
            ) : null}
            {sessionId && conversationQuery.isLoading ? (
              <p className="text-muted-foreground">Loading…</p>
            ) : null}
            {conversationQuery.error ? (
              <p className="text-danger">Failed to load conversation.</p>
            ) : null}
            {turns.map((turn) => (
              <TurnView
                key={turn.turn_index}
                turn={turn}
                onCite={onCite}
                highlight={highlight}
              />
            ))}
            {pendingQuestion ? (
              <div className="flex flex-col gap-3">
                <div className="max-w-[80%] self-end rounded-[14px_14px_4px_14px] bg-primary px-[15px] py-2.5 text-[13.5px] leading-[1.5] text-primary-foreground">
                  {pendingQuestion}
                </div>
                <div className="rounded-[14px_14px_14px_4px] border border-border bg-card px-[18px] py-4 text-sm text-muted-foreground shadow-[var(--shadow)]">
                  Thinking…
                </div>
              </div>
            ) : null}
            <div ref={latestTurnRef} aria-hidden="true" />
          </div>
        </div>

        <div className="border-t border-border bg-muted px-[30px] py-3.5">
          {mutation.isError ? (
            <p className="mx-auto mb-2 max-w-[760px] text-sm text-danger">
              Failed to send question.
            </p>
          ) : null}
          <div className="mx-auto flex max-w-[760px] items-end gap-2.5">
            <Textarea
              value={question}
              disabled={mutation.isPending}
              placeholder="Ask a legal question…"
              className="min-h-[52px] flex-1 resize-none rounded-[11px] border-border-strong bg-card"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <Button
              className="self-end px-5"
              disabled={mutation.isPending}
              onClick={send}
            >
              Send
            </Button>
          </div>
          <p className="mx-auto mt-2 max-w-[760px] text-[11px] text-faint">
            Answers are grounded in the indexed primary sources and cite article/section
            numbers. The system abstains when the corpus can't support an answer.
          </p>
        </div>
      </section>
    </div>
  );
}
