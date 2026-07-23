import type { ChatSource } from "@/api/client";

export function citedRefs(answer: string): Set<number> {
  return new Set([...answer.matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1])));
}

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
          className="mx-px inline-block cursor-pointer rounded-[4px] border border-primary-bd bg-primary-bg px-1 align-[1px] font-mono text-[11px] font-semibold text-primary hover:brightness-95"
          onClick={() => onCite?.(n)}
          aria-label={`Citation ${n}`}
        >
          [{n}]
        </button>,
      );
    } else {
      parts.push(m[0]);
    }
    last = m.index + m[0].length;
  }
  if (last < answer.length) parts.push(answer.slice(last));
  return parts;
}
