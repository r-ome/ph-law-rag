import type { EvalRow } from "@/api/client";

export type RowFilters = {
  search: string; // case-insensitive substring on question + eval_id
  category: string | "all";
  split: string | "all";
  verdict: "all" | "sufficient" | "partial" | "insufficient" | "none"; // none = evidence null
  abstainedOnly: boolean;
  sourceMissOnly: boolean; // expected_missing.length > 0
};

export const defaultRowFilters: RowFilters = {
  search: "",
  category: "all",
  split: "all",
  verdict: "all",
  abstainedOnly: false,
  sourceMissOnly: false,
};

export function filterRows(rows: EvalRow[], f: RowFilters): EvalRow[] {
  const search = f.search.trim().toLowerCase();
  return rows.filter((row) => {
    if (search) {
      const inQuestion = row.question.toLowerCase().includes(search);
      const inId = (row.eval_id ?? "").toLowerCase().includes(search);
      if (!inQuestion && !inId) return false;
    }
    if (f.category !== "all" && row.category !== f.category) return false;
    if (f.split !== "all" && row.split !== f.split) return false;
    if (f.verdict !== "all") {
      if (f.verdict === "none") {
        if (row.evidence != null) return false;
      } else if (row.evidence?.verdict !== f.verdict) {
        return false;
      }
    }
    if (f.abstainedOnly && !row.abstained) return false;
    if (f.sourceMissOnly && row.expected_missing.length === 0) return false;
    return true;
  });
}

export function verdictBadgeVariant(
  verdict: string | null | undefined,
): "outline" | "secondary" | "destructive" {
  if (verdict === "partial") return "secondary";
  if (verdict === "insufficient") return "destructive";
  return "outline";
}
