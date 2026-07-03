from textwrap import shorten

from app.retriever.context_selection import select_context
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
    if metadata.get("unit_label"):
        lines.append(f"    unit_label={metadata.get('unit_label')}")
    if metadata.get("provision_id"):
        lines.append(f"    provision_id={metadata.get('provision_id')}")
    if url:
        lines.append(f"    url={url}")
    lines.append(f"    preview={preview}")
    return "\n".join(lines)


def retrieve(query_text: str) -> str:
    selection = select_context(query_text)
    top = selection.selected
    expanded = len(top) != len(selection.pre_expansion) or any(
        r.metadata.get("expanded_from_parent") for r in top
    )

    lines = [
        f"Query: {query_text}",
        f"Retrieved: {len(selection.retrieved)}",
        f"Pre-expansion: {len(selection.pre_expansion)}",
        f"Selected: {len(top)}" + (" (parent-expanded/deduped)" if expanded else ""),
    ]

    if not top:
        lines.append("No matching chunks found.")
        return "\n".join(lines)

    lines.append("")
    lines.extend(_format_result(index, result) for index, result in enumerate(top, start=1))
    return "\n\n".join(lines)
