from app.retriever.types import RetrievalResult

# Units whose numbers reset per parent → show the parent path for disambiguation.
# Globally-unique units (Article in the codes) don't need it.
_PATH_UNITS = {"section", "rule"}

def _pretty(seg: str) -> str:
      # "ARTICLE III" -> "Article III"; "BOOK IV" -> "Book IV"; keep roman numerals as-is
      head, _, rest = seg.partition(" ")
      return f"{head.capitalize()} {rest}".strip() if rest else head.capitalize()

def _locator(m: dict) -> str | None:
      """Pinpoint citation string from chunk metadata, or None for prose chunks."""
      if not m.get("is_structural") or not m.get("unit_label"):
          return None
      label = _pretty(m["unit_label"])
      path = m.get("structure_path")
      if m.get("unit_type") in _PATH_UNITS and path:
          prefix = ", ".join(_pretty(p) for p in path.split(" > "))
          return f"{prefix}, {label}"
      return label

def build_context(results: list[RetrievalResult]) -> tuple[str, list[dict]]:
      blocks = []
      sources = []
      for i, r in enumerate(results, start=1):
          title = r.metadata.get("title", "Unknown source")
          url = r.metadata.get("url", "")
          locator = _locator(r.metadata)
          via = r.metadata.get("_edge_relation")
          header = f"[{i}] {title}"
          if locator:
              header += f", {locator}"
          if via:
              header += f" ({via})"
          if url:
              header += f" — {url}"
          blocks.append(f"{header}\n{r.text}")
          sources.append({
              "ref": i,
              "title": title,
              "url": url,
              "source_id": r.metadata.get("source_id", ""),
              "locator": locator,
              "via": via,
          })
      return "\n\n".join(blocks), sources