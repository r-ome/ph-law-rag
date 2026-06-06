from app.retriever.types import RetrievalResult

def build_context(results: list[RetrievalResult]) -> tuple[str, list[dict]]:
      blocks = []
      sources = []
      for i, r in enumerate(results, start=1):
          title = r.metadata.get("title", "Unknown source")
          url = r.metadata.get("url", "")
          header = f"[{i}] {title}"
          if url:
              header += f" — {url}"
          blocks.append(f"{header}\n{r.text}")
          sources.append({
              "ref": i,
              "title": title,
              "url": url,
              "source_id": r.metadata.get("source_id", ""),
          })
      return "\n\n".join(blocks), sources