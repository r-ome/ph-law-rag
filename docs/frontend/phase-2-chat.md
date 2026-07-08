# Phase 2 — Chat + citations + read-only conversations

**Goal.** Add a working chat page over the existing `/query/ask` pipeline: a threaded
message view with inline `[n]` citations that resolve to source cards, and a sidebar of past
conversations that replay with their citations intact. Blocking request/response (no
streaming). Read-only conversations (list + get; new threads are still created implicitly by
`/query/ask`).

**Executor guidance.** Backend code below is exact — type it verbatim (field names/types are
the codegen contract). Frontend code is pinned for the client, routing, response models, and
the citation-parsing contract; fill idiomatic React/JSX around the given shapes. Do not add
endpoints, pages, or shadcn components beyond those listed. Keep business logic in Python
service modules — routes stay thin adapters. Stop at the acceptance checks.

**Preconditions.**
- Phase 0 + Phase 1 complete and green (scaffold, strict TS, codegen, corpus browser).
- Backend runs locally: `uvicorn app.api.main:app --reload` on `:8000` against a **synced +
  indexed** DB (Qdrant up, Ollama up, or the Anthropic generator env set) so `/query/ask`
  returns real answers with `sources`.
- Working dir for all `npm` commands is `frontend/`.
- shadcn present so far: `table button badge input select scroll-area`. Phase 2 **authorizes
  three additions only**: `textarea`, `card`, `separator` (the Phase 1 "no new components"
  constraint was phase-scoped). Add them via the shadcn CLI; patch inline for strict TS as in
  Phase 0/1. Add nothing else.

---

## Part A — Backend: response model on `/query/ask`, sources-per-turn migration, conversation readers + routes

### A0. Context — what already exists (do not rebuild)

- `answer(question, debug, session_id, ...)` (`app/retriever/answer_service.py:229`) returns a
  dict `{answer, sources[], contexts[], context_sources[], abstained, error}` and, when
  `session_id` is set, **already** appends a turn via `append_turn` at
  `answer_service.py:294`. The generator emits inline `[n]` markers whose numbers match
  `sources[].ref` (`app/retriever/prompts.py:49`).
- `sources[]` element shape (`app/retriever/context_builder.py:39`):
  `{ref: int, title: str, url: str, source_id: str, locator: str | None, via: str | None}`.
  **`locator` and `via` are frequently `None`** (non-structural chunk / no edge expansion) —
  the response model **must** allow null or valid answers will fail validation.
- `app/conversation/session.py` has `create_session`, `session_exists`, `get_history`,
  `append_turn`. Tables `conversations(session_id, created_at, title)` and
  `conversation_turns(turn_id, session_id, turn_index, question, rewritten_question, answer,
  retrieved_chunks_json, created_at)` exist (migration 3, `app/db.py:94`).
- **Gap this phase closes:** `append_turn` persists only `retrieved_chunks_json` = a JSON list
  of `chunk_id` strings (`answer_service.py:301`). The citation `sources[]` are **not** stored,
  so a replayed turn would show inline `[n]` markers with nothing to resolve them against.
  Phase 2 persists `sources_json` per turn.

### A1. Migration 4 — add `sources_json` to `conversation_turns` — `app/db.py`

**Do not edit the migration-3 `CREATE TABLE conversation_turns` block** — existing DBs already
applied migration 3, so a changed `CREATE TABLE` never runs. Append a **new** tuple to the
`MIGRATIONS` list (after the migration-3 tuple, before the closing `]` at `app/db.py:118`).
Match the existing tab indentation and `(version, description, sql)` shape:

```python
	(
		4,
		"add sources_json to conversation_turns",
		"""
		ALTER TABLE conversation_turns ADD COLUMN sources_json TEXT;
		""",
	),
```

Run `raglab init` (or restart the API — `init_db()` applies pending migrations) so migration 4
lands on the local DB.

### A2. Persist sources on each turn — `app/retriever/answer_service.py`

At the existing `append_turn` call (`answer_service.py:297`), add the `sources_json` field.
`response["sources"]` is already computed; serialize it. Leave everything else unchanged.

```python
        if session_id:
            import json
            from app.conversation.session import append_turn
            append_turn(session_id, {
                "question": question,                       # original, not rewritten
                "rewritten_question": effective_question,
                "answer": response["answer"],
                "retrieved_chunks_json": json.dumps([r.chunk_id for r in selection.selected]),
                "sources_json": json.dumps(response.get("sources", [])),
            })
```

Then extend `append_turn` in `app/conversation/session.py` to write the new column. Update the
INSERT column list, placeholders, and value tuple (add `sources_json` after
`retrieved_chunks_json`):

```python
        conn.execute(
            """
            INSERT INTO conversation_turns(
                turn_id, session_id, turn_index, question,
                rewritten_question, answer, retrieved_chunks_json, sources_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                turn_id, session_id, next_index, turn["question"],
                turn.get("rewritten_question"), turn.get("answer"),
                turn.get("retrieved_chunks_json"), turn.get("sources_json"), _now(),
            ),
        )
```

### A3. Conversation readers — `app/conversation/session.py`

Add two readers. `list_conversations()` derives a title **lazily** from the first turn's
question when `conversations.title IS NULL` (no route/`answer()` change needed — this sidesteps
the two auto-create call sites). `get_conversation()` returns turns with a **null-safe**
`sources` parse: `NULL`/invalid `sources_json` → `[]` (old turns replay with no source cards,
answer text still shows).

```python
def _truncate_title(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def list_conversations() -> list[dict]:
    """Sessions newest-first, with turn_count and a title (lazy: first question if unset)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                c.session_id,
                c.created_at,
                c.title,
                (SELECT COUNT(*) FROM conversation_turns t WHERE t.session_id = c.session_id)
                    AS turn_count,
                (SELECT t.question FROM conversation_turns t
                 WHERE t.session_id = c.session_id
                 ORDER BY t.turn_index ASC LIMIT 1) AS first_question
            FROM conversations c
            ORDER BY c.created_at DESC
            """
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        title = d["title"] or (
            _truncate_title(d["first_question"]) if d["first_question"] else "New conversation"
        )
        out.append(
            {
                "session_id": d["session_id"],
                "created_at": d["created_at"],
                "title": title,
                "turn_count": d["turn_count"],
            }
        )
    return out


def get_conversation(session_id: str) -> dict | None:
    """Full thread oldest-first, each turn carrying its persisted citation sources."""
    import json

    conn = get_connection()
    try:
        if conn.execute(
            "SELECT 1 FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone() is None:
            return None
        rows = conn.execute(
            """
            SELECT turn_index, question, answer, sources_json
            FROM conversation_turns
            WHERE session_id = ?
            ORDER BY turn_index ASC
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    turns: list[dict] = []
    for r in rows:
        raw = r["sources_json"]
        try:
            sources = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            sources = []
        turns.append(
            {
                "turn_index": r["turn_index"],
                "question": r["question"],
                "answer": r["answer"] or "",
                "sources": sources,
            }
        )
    return {"session_id": session_id, "turn_count": len(turns), "turns": turns}
```

### A4. Typed response model on `/query/ask` — `app/api/routes_query.py`

`/query/ask` currently returns a bare dict (no `response_model`) — the one endpoints-first
invariant violation in the API. Add a `response_model` so `ask` contributes typed schema to
`/openapi.json`. Chat contract = `answer + sources + abstained + error + session_id`;
`contexts`/`context_sources`/`debug` stay **off** the wire (Phase 4 owns retrieval/debug
payloads). FastAPI drops response fields not on the model, so the extra keys in the `answer()`
dict are simply not serialized. Full file:

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.retriever.answer_service import answer
from app.conversation.session import create_session

router = APIRouter(prefix="/query", tags=["query"])


class Source(BaseModel):
    ref: int
    title: str
    url: str
    source_id: str
    locator: str | None = None
    via: str | None = None


class AskRequest(BaseModel):
    question: str
    session_id: str | None = None
    debug: bool | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[Source] = []
    abstained: bool = False
    error: bool = False
    session_id: str


@router.post("/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    session_id = request.session_id or create_session()  # API auto-threads
    result = answer(request.question, debug=request.debug, session_id=session_id, trace_label="api")
    result["session_id"] = session_id
    return AskResponse(**result)
```

> `AskResponse(**result)` ignores unlisted keys? No — `BaseModel(**dict)` with extra keys
> **errors** by default only if `model_config` forbids extras; Pydantic v2 **ignores** unknown
> kwargs on construction. `result` carries `contexts`/`context_sources`/maybe `debug`; those
> are silently dropped. If your Pydantic config sets `extra="forbid"` globally, switch to
> `AskResponse.model_validate(result)` (which ignores extras) instead — verify against A6.

### A5. Conversation response models + routes — new file `app/api/routes_conversations.py`

Thin adapters over the A3 readers. Register in `main.py`.

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.routes_query import Source
from app.conversation.session import get_conversation, list_conversations

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationSummary(BaseModel):
    session_id: str
    title: str
    created_at: str
    turn_count: int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]


class ConversationTurn(BaseModel):
    turn_index: int
    question: str
    answer: str
    sources: list[Source] = []


class ConversationDetail(BaseModel):
    session_id: str
    turn_count: int
    turns: list[ConversationTurn]


@router.get("", response_model=ConversationListResponse, summary="List conversations")
def conversations() -> ConversationListResponse:
    return ConversationListResponse(conversations=list_conversations())


@router.get(
    "/{session_id}",
    response_model=ConversationDetail,
    summary="Full conversation with per-turn citations",
)
def conversation_detail(session_id: str) -> ConversationDetail:
    detail = get_conversation(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationDetail(**detail)
```

Register the router in `app/api/main.py` (add beside the existing includes):

```python
from app.api.routes_conversations import router as conversations_router
...
app.include_router(conversations_router)
```

### A6. Verify backend

```bash
uvicorn app.api.main:app --reload            # repo root, separate shell; DB migrated + indexed

# 1. ask returns the typed chat shape + a session_id; capture the session
RESP=$(curl -s -X POST localhost:8000/query/ask -H 'content-type: application/json' \
  -d '{"question":"What are the penalties for theft under the Revised Penal Code?"}')
echo "$RESP" | python -m json.tool | grep -E '"answer"|"sources"|"abstained"|"session_id"|"ref"|"locator"' | head
echo "$RESP" | python -c "import sys,json;d=json.load(sys.stdin);print('has_contexts', 'contexts' in d)"   # expect False
SID=$(echo "$RESP" | python -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

# 2. the conversation lists and replays WITH its citations (core risk: no dangling [n])
curl -s localhost:8000/conversations | python -m json.tool | grep -E '"session_id"|"title"|"turn_count"' | head
curl -s "localhost:8000/conversations/$SID" | python -c "import sys,json;d=json.load(sys.stdin);t=d['turns'][0];print('turns',d['turn_count'],'sources',len(t['sources']),'answer_has_marker','[1]' in t['answer'])"

# 3. unknown conversation → 404
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/conversations/__nope__     # expect 404

# 4. openapi exposes the new models
curl -s localhost:8000/openapi.json | python -m json.tool | grep -E 'AskResponse|Source|ConversationDetail|ConversationSummary'
```

**Backend acceptance:**
- `POST /query/ask` returns `{answer, sources[], abstained, error, session_id}` only —
  `contexts`/`context_sources`/`debug` absent; `sources[].locator`/`via` may be `null`.
- After one ask, `GET /conversations/{session_id}` returns the turn with a **non-empty**
  `sources[]` and every inline `[n]` in `answer` resolves to a `sources[].ref` (this is the
  feature's core correctness check — no dangling citations on replay).
- `GET /conversations` lists sessions newest-first with a derived `title` + `turn_count`;
  unknown id → 404.
- `/openapi.json` exposes `AskResponse`, `Source`, `ConversationListResponse`,
  `ConversationSummary`, `ConversationDetail`, `ConversationTurn`; `/docs` renders them.

---

## Part B — Frontend: regen types + typed client

### B1. Regenerate types (backend must be up)

```bash
npm run gen:types        # rewrites src/api/schema.ts ONLY — adds no client functions
```

Commit the regenerated `src/api/schema.ts`. `gen:types` regenerates types only; the client
functions in B2 are hand-added.

### B2. Client — extend `src/api/client.ts`

Keep the existing `apiGet` + Phase 1 getters. **Manually add** the ask/conversation types and
three functions, all derived from `paths` — no hand-written request/response shapes.

```ts
import type { paths } from "@/api/schema";

type AskBody =
  paths["/query/ask"]["post"]["requestBody"]["content"]["application/json"];
type AskResponse =
  paths["/query/ask"]["post"]["responses"]["200"]["content"]["application/json"];
type ConversationListResponse =
  paths["/conversations"]["get"]["responses"]["200"]["content"]["application/json"];
type ConversationDetail =
  paths["/conversations/{session_id}"]["get"]["responses"]["200"]["content"]["application/json"];

export type ChatSource = AskResponse["sources"][number];
export type ConversationSummary = ConversationListResponse["conversations"][number];
export type ConversationTurn = ConversationDetail["turns"][number];

// apiGet stays as in Phase 1.

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return (await res.json()) as T;
}

export function ask(body: AskBody): Promise<AskResponse> {
  return apiPost<AskResponse>("/query/ask", body);
}

export function listConversations(): Promise<ConversationListResponse> {
  return apiGet<ConversationListResponse>("/conversations");
}

export function getConversation(sessionId: string): Promise<ConversationDetail> {
  return apiGet<ConversationDetail>(`/conversations/${encodeURIComponent(sessionId)}`);
}
```

---

## Part C — Frontend: routing, chat page, citation rendering

### C1. Router + nav — `src/App.tsx`

Add a `/chat` route (and a `/chat/:sessionId` variant for a selected thread) and a nav link
beside the existing Corpus link. Keep Phase 1 routes.

```tsx
import { Link, Route, Routes } from "react-router-dom";
import CorpusList from "@/routes/CorpusList";
import CorpusDetail from "@/routes/CorpusDetail";
import Chat from "@/routes/Chat";

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b px-6 py-3 flex items-center gap-6">
        <Link to="/" className="text-lg font-semibold">PH Law RAG — Workbench</Link>
        <nav className="flex gap-4 text-sm">
          <Link to="/">Corpus</Link>
          <Link to="/chat">Chat</Link>
        </nav>
      </header>
      <main className="p-6">
        <Routes>
          <Route path="/" element={<CorpusList />} />
          <Route path="/documents/:docId" element={<CorpusDetail />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/chat/:sessionId" element={<Chat />} />
        </Routes>
      </main>
    </div>
  );
}
```

### C2. Citation parser — `src/lib/citations.tsx`

The one piece of nontrivial UI logic. Split an answer string on inline `[n]` markers and render
each marker as a clickable chip that scrolls to / highlights the matching source card. A marker
whose `n` has no matching `ref` renders as plain text (defensive — never throw). Support
grouped markers `[2][3]` (already separate matches) and keep this pure/testable.

```tsx
import type { ChatSource } from "@/api/client";

// Renders answer text with [n] markers turned into chips linking to sources by `ref`.
export function renderAnswerWithCitations(
  answer: string,
  sources: ChatSource[],
  onCite?: (ref: number) => void,
): React.ReactNode[] {
  const refs = new Set(sources.map((s) => s.ref));
  const parts: React.ReactNode[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(answer)) !== null) {
    const n = Number(m[1]);
    if (m.index > last) parts.push(answer.slice(last, m.index));
    if (refs.has(n)) {
      parts.push(
        <button
          key={`cite-${key++}`}
          type="button"
          className="mx-0.5 rounded bg-muted px-1 text-xs font-medium text-primary hover:underline"
          onClick={() => onCite?.(n)}
          aria-label={`Citation ${n}`}
        >
          [{n}]
        </button>,
      );
    } else {
      parts.push(m[0]); // no matching source → plain text, never throw
    }
    last = m.index + m[0].length;
  }
  if (last < answer.length) parts.push(answer.slice(last));
  return parts;
}
```

### C3. Chat page — `src/routes/Chat.tsx`

A single page: a conversation sidebar + a message thread + a composer. Blocking send.

**State & data:**
- `const { sessionId } = useParams()` (`string | undefined`); the active session. When present,
  load history: `useQuery({ queryKey: ["conversation", sessionId], queryFn: () =>
  getConversation(sessionId!), enabled: Boolean(sessionId) })`.
- Sidebar: `useQuery({ queryKey: ["conversations"], queryFn: listConversations })`. Each item
  links to `/chat/${session_id}`; show `title` + `turn_count`. A "New chat" `Link to="/chat"`
  clears the active session.
- Local `messages` state for the **current unsaved exchange** while a send is in flight; on the
  first send with no `sessionId`, capture the returned `session_id` and `navigate(\`/chat/
  ${id}\`)` so the thread persists and the sidebar/history queries pick it up
  (`queryClient.invalidateQueries(["conversations"])`).

**Send (blocking, with loading state):**
```ts
const mutation = useMutation({
  mutationFn: (question: string) =>
    ask({ question, session_id: sessionId ?? null }),
  onSuccess: (res) => {
    if (!sessionId) navigate(`/chat/${res.session_id}`);
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
    queryClient.invalidateQueries({ queryKey: ["conversation", res.session_id] });
  },
});
```
- While `mutation.isPending`: disable the composer, append an optimistic user bubble, and show
  a **"Thinking…" skeleton** assistant bubble (answers can take ~30s on the host — the memory
  notes Bedrock reranker quota pacing). `mutation.isError` → red inline error, re-enable
  composer, keep the typed question.

**Message rendering:**
- Replay persisted turns from the `conversation` query (each `turn` → a user bubble
  `turn.question` + an assistant bubble rendering `turn.answer` via
  `renderAnswerWithCitations(turn.answer, turn.sources, onCite)`).
- Under each assistant bubble, a **source cards** list from `turn.sources`: a shadcn `card` per
  source showing `[ref] title`, `locator` (when non-null), `via` (when non-null, as a muted
  tag), and `url` as an external `<a target="_blank" rel="noreferrer">`. Give each card
  `id={`src-${turn.turn_index}-${ref}`}`; `onCite(ref)` scrolls to / briefly highlights it.
- Composer: shadcn `textarea` + a Send `button` (Enter submits, Shift+Enter newline). Empty/
  whitespace-only is a no-op.

**Empty states:** no active session → a centered prompt ("Ask a question about Philippine
law."). Loading history → "Loading…". Query error → red text.

Use shadcn `card` + `separator` for layout; `textarea` for the composer. No other new
components.

---

## Part D — Tests

### D1. Unit — `src/lib/citations.test.tsx`

Pure-function tests for `renderAnswerWithCitations`:
- `"Theft is punished [1] under Art. 309 [2]."` with sources `ref: 1,2` → two citation buttons;
  clicking one calls `onCite` with the right ref.
- A marker with **no** matching source (`"... [9]"`, sources only `ref:1`) renders as plain text
  and fires no `onCite`.
- Grouped `"[2][3]"` yields two separate buttons.

### D2. Unit — extend `src/api/client.test.ts`

- `ask({question:"x"})` POSTs to `/api/query/ask` with a JSON body (assert method + URL + body)
  and returns the typed shape.
- `getConversation("abc")` GETs `/api/conversations/abc`.

### D3. E2E mocked — `e2e/chat.mocked.spec.ts`

Default lane, **no backend**. Intercept:
- `POST **/api/query/ask` → `{ answer: "Theft is punished [1].", sources: [{ref:1, title:"Revised Penal Code", url:"https://…", source_id:"rpc_1930", locator:"Article 309", via:null}], abstained:false, error:false, session_id:"sess-1" }`.
- `**/api/conversations` → `{ conversations: [{session_id:"sess-1", title:"Theft…", created_at:"…", turn_count:1}] }`.
- `**/api/conversations/sess-1` → a `ConversationDetail` with one turn whose `answer` contains
  `[1]` and `sources` has the matching `ref:1`.

Assert: typing a question + Send shows a "Thinking…" state then the answer; the `[1]` chip
renders and clicking it reveals/scrolls the source card; navigating to `/chat/sess-1` **replays
the turn with its citation resolved** (no dangling `[1]`). Mirror generated `schema.ts` shapes
in fixtures.

### D4. E2E smoke (optional, real backend) — `e2e/chat.smoke.spec.ts`

`--project=smoke`, manual/nightly with backend up + indexed: open `/chat`, ask one real legal
question, assert an answer bubble renders with ≥1 source card, then reload the thread URL and
assert the citation still resolves. No mocks. (Allow a long timeout — host answers pace ~30s.)

---

## Acceptance criteria (all must pass)

Backend:
- [ ] `POST /query/ask` has `response_model=AskResponse`; returns `{answer, sources[], abstained,
      error, session_id}` with `contexts`/`context_sources`/`debug` **absent**; `sources[].
      locator`/`via` nullable.
- [ ] Migration 4 adds `conversation_turns.sources_json` as a **new** migration (existing DBs
      upgrade cleanly); `append_turn` persists it.
- [ ] `GET /conversations` lists sessions newest-first with derived `title` + `turn_count`.
- [ ] `GET /conversations/{id}` replays turns each carrying `sources[]`; every inline `[n]` in a
      turn's `answer` resolves to a `sources[].ref`; old (pre-migration) turns return `sources:
      []` without error; unknown id → **404**.
- [ ] `/openapi.json` exposes `AskResponse`, `Source`, `ConversationListResponse`,
      `ConversationSummary`, `ConversationDetail`, `ConversationTurn`.

Frontend (run in `frontend/`):
- [ ] `npm run gen:types` regenerates `src/api/schema.ts` clean; committed.
- [ ] `npm run typecheck` clean under strict (incl. any patched `src/components/ui/**` for the
      three new shadcn components).
- [ ] `npm run dev` + backend up: `/chat` sends a question (blocking, with a "Thinking…"
      state), renders the answer with `[n]` citation chips + source cards; the sidebar lists
      threads; selecting one replays it with citations intact.
- [ ] `npm run test:unit` passes (citation parser + client tests).
- [ ] `npx playwright test --project=mocked` passes with **no backend running**, including the
      replayed-citation assertion.
- [ ] `src/api/client.ts` derives all types from `@/api/schema` — no hand-written request/
      response interfaces.

## Out of scope (do NOT build here)

- SSE / token streaming (blocking POST only this phase).
- Conversation create / delete / rename endpoints or UI (threads auto-created by `/query/ask`;
  list + get only).
- Debug / retrieval-trace panel, full chunk inspection (Phase 4 — Retrieval Lab).
- Dashboard / Ingestion / Health / Sources (Phase 3).
- Editing the corpus, dark mode, auth, global nav chrome beyond the two nav links.
- New shadcn components beyond `textarea`, `card`, `separator`. Touching the Streamlit app.

## Handoff notes for the executor

- `gen:types` and the `smoke` lane need the backend on `:8000` (indexed); `mocked` +
  `test:unit` must not.
- Apply migration 4 locally (`raglab init` or restart the API) **before** hitting `/query/ask`,
  or `append_turn` will fail on the missing column.
- Verify the A4 note: if the project's Pydantic config sets `extra="forbid"`, use
  `AskResponse.model_validate(result)` instead of `AskResponse(**result)`. Confirm via the A6
  curl that `contexts` is absent and the call doesn't 500.
- Under `exactOptionalPropertyTypes`, `session_id: sessionId ?? null` (not `undefined`) matches
  the generated `AskBody` optional-nullable field; guard `useParams` `sessionId` before use.
- If a shadcn `ui/` file errors under strict TS, fix inline — never loosen tsconfig or add
  `// @ts-ignore`.
- Report back: `tsc -b` output; the A6 curl results (ask shape incl. absent `contexts`; the
  replayed-conversation `sources` count + `[1]`-resolves check; the 404); and `test:unit` +
  `--project=mocked` results.
