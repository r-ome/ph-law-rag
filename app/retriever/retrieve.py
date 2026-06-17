from textwrap import shorten

from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.types import RetrievalResult


def _format_result(index: int, result: RetrievalResult) -> str:
    metadata = result.metadata
    source_id = metadata.get("source_id", "unknown")
    title = metadata.get("title", "Untitled source")
    url = metadata.get("url", "")
    preview = shorten(" ".join(result.text.split()), width=240, placeholder="...")

    lines = [
        f"[{index}] score={result.score:.4f} chunk_id={result.chunk_id}",
        f"    source={source_id}",
        f"    title={title}",
    ]
    if url:
        lines.append(f"    url={url}")
    lines.append(f"    preview={preview}")
    return "\n".join(lines)


def retrieve(query_text: str) -> str:
    hits = hybrid_retriever(query_text)
    top = rerank(query_text, hits)

    lines = [
        f"Query: {query_text}",
        f"Retrieved: {len(hits)}",
        f"Reranked: {len(top)}",
    ]

    if not top:
        lines.append("No matching chunks found.")
        return "\n".join(lines)

    lines.append("")
    lines.extend(_format_result(index, result) for index, result in enumerate(top, start=1))
    return "\n\n".join(lines)
