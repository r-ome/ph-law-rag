from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol, TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.retriever.context_selection import SelectionResult


@dataclass(frozen=True)
class RetrievalKnobs:
    dense_top_k: int
    sparse_top_k: int
    rerank_top_n: int
    parent_expansion_enabled: bool
    prefer_operative_enabled: bool
    retrieval_operative_only: bool
    consolidated_dedup_enabled: bool

    @classmethod
    def from_settings(cls) -> "RetrievalKnobs":
        return cls(
            dense_top_k=settings.dense_top_k,
            sparse_top_k=settings.sparse_top_k,
            rerank_top_n=settings.rerank_top_n,
            parent_expansion_enabled=settings.parent_expansion_enabled,
            prefer_operative_enabled=settings.prefer_operative_enabled,
            retrieval_operative_only=settings.retrieval_operative_only,
            consolidated_dedup_enabled=settings.consolidated_dedup_enabled,
        )

    def as_trace_dict(self) -> dict:
        return asdict(self)


class Strategy(Protocol):
    name: str

    def execute(self, question: str, knobs: RetrievalKnobs | None = None) -> "SelectionResult":
        ...


@dataclass(frozen=True)
class StrategyPreset:
    name: str

    def execute(self, question: str, knobs: RetrievalKnobs | None = None) -> "SelectionResult":
        from app.retriever.context_selection import select_context

        return select_context(question, knobs=knobs or resolve_knobs(self.name))


_PRESET_KNOBS: dict[str, RetrievalKnobs | None] = {
    "default": None,
}

# Candidate stubs kept out of STRATEGIES until R3 trace checks justify a real knob diff.
CANDIDATE_PRESET_STUBS = ("citation_precision", "current_law")

STRATEGIES: dict[str, Strategy] = {
    "default": StrategyPreset("default"),
}


def resolve_knobs(strategy_name: str) -> RetrievalKnobs:
    try:
        preset = _PRESET_KNOBS[strategy_name]
    except KeyError as exc:
        raise ValueError(f"Unknown retrieval strategy: {strategy_name}") from exc
    if preset is None:
        return RetrievalKnobs.from_settings()
    return preset
