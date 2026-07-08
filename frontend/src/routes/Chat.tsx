import { useMemo, useState } from "react";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";

type Highlight = { turnIndex: number; ref: number } | null;
type SourceGroup = ChatSource & { refs: number[] };

function sourceKey(source: ChatSource) {
  return [
    source.source_id,
    source.title,
    source.url,
    source.locator ?? "",
    source.via ?? "",
  ].join("\u001f");
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
    <div className="mt-3 grid gap-2">
      {sourceGroups.map((source) => {
        const active =
          highlight != null && highlight.turnIndex === turnIndex && source.refs.includes(highlight.ref);
        const refs = source.refs.join(", ");
        return (
          <Card
            key={`${turnIndex}-${sourceKey(source)}`}
            id={`src-${turnIndex}-${source.refs[0]}`}
            size="sm"
            className={active ? "ring-2 ring-primary" : undefined}
          >
            <CardHeader>
              {source.refs.slice(1).map((ref) => (
                <span key={ref} id={`src-${turnIndex}-${ref}`} className="sr-only" />
              ))}
              <CardTitle className="text-sm">
                [{refs}] {source.title}
              </CardTitle>
              {source.locator && <CardDescription>{source.locator}</CardDescription>}
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              {source.via && (
                <Badge variant="secondary" className="w-fit">
                  {source.via}
                </Badge>
              )}
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="block break-all hover:underline"
              >
                {source.url}
              </a>
            </CardContent>
          </Card>
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
    <div className="space-y-3">
      <div className="ml-auto max-w-[78ch] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
        {turn.question}
      </div>
      <div className="max-w-[82ch] rounded-lg border bg-card px-3 py-2 text-sm leading-6">
        <div className="whitespace-pre-wrap">
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

  const conversationsQuery = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
  });

  const conversationQuery = useQuery({
    queryKey: ["conversation", sessionId],
    queryFn: () => getConversation(sessionId!),
    enabled: Boolean(sessionId),
  });

  const turns = useMemo(() => conversationQuery.data?.turns ?? [], [conversationQuery.data]);

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
    <div className="grid h-[calc(100vh-7rem)] min-h-0 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col rounded-lg border bg-card">
        <div className="flex items-center justify-between px-3 py-3">
          <h2 className="text-sm font-semibold">Conversations</h2>
          <Button variant="outline" size="sm" nativeButton={false} render={<Link to="/chat" />}>
            New chat
          </Button>
        </div>
        <Separator />
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {conversationsQuery.isLoading && <p className="p-2 text-sm">Loading…</p>}
          {conversationsQuery.error && (
            <p className="p-2 text-sm text-red-600">Failed to load conversations.</p>
          )}
          <div className="space-y-1">
            {(conversationsQuery.data?.conversations ?? []).map((c) => (
              <Link
                key={c.session_id}
                to={`/chat/${c.session_id}`}
                className={
                  c.session_id === sessionId
                    ? "block rounded-md bg-muted px-2 py-2 text-sm text-foreground"
                    : "block rounded-md px-2 py-2 text-sm text-foreground hover:bg-muted"
                }
              >
                <span className="block truncate font-medium">{c.title}</span>
                <span className="text-xs text-muted-foreground">{c.turn_count} turns</span>
              </Link>
            ))}
          </div>
        </div>
      </aside>

      <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border bg-background">
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {!sessionId && turns.length === 0 && !pendingQuestion && (
            <div className="flex h-full items-center justify-center text-muted-foreground">
              Ask a question about Philippine law.
            </div>
          )}
          {sessionId && conversationQuery.isLoading && <p>Loading…</p>}
          {conversationQuery.error && (
            <p className="text-red-600">Failed to load conversation.</p>
          )}
          <div className="space-y-6">
            {turns.map((turn) => (
              <TurnView
                key={turn.turn_index}
                turn={turn}
                onCite={onCite}
                highlight={highlight}
              />
            ))}
            {pendingQuestion && (
              <div className="space-y-3">
                <div className="ml-auto max-w-[78ch] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
                  {pendingQuestion}
                </div>
                <div className="max-w-[82ch] rounded-lg border bg-card px-3 py-2 text-sm text-muted-foreground">
                  Thinking…
                </div>
              </div>
            )}
          </div>
        </div>
        <Separator />
        <div className="p-3">
          {mutation.isError && (
            <p className="mb-2 text-sm text-red-600">Failed to send question.</p>
          )}
          <div className="flex gap-2">
            <Textarea
              value={question}
              disabled={mutation.isPending}
              placeholder="Ask a legal question…"
              className="min-h-20 resize-none"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <Button className="self-end" disabled={mutation.isPending} onClick={send}>
              Send
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
