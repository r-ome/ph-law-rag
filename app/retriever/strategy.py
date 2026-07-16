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
    sparse_overfetch_k: int = 100
    rerank_score_margin: float = 6.0
    max_distance: float = 0.5
    edge_expansion_enabled: bool = True
    edge_hop_top_k: int = 3
    parent_expansion_min_children: int = 2
    parent_expansion_max_chars: int = 8000
    query_planner_model: str = "mistral"
    query_planner_max_subqueries: int = 3
    query_decomposition_enabled: bool = False
    reranker_backend: str | None = None
    subquery_packaging_enabled: bool = False
    subquery_reserve_n: int = 2
    sibling_expansion_enabled: bool = False
    sibling_expansion_radius: int = 1
    sibling_expansion_max_chars: int = 3000
    sibling_expansion_max_tokens: int = 750

    @classmethod
    def from_settings(cls, settings_obj=settings) -> "RetrievalKnobs":
        return cls(
            dense_top_k=settings_obj.dense_top_k,
            sparse_top_k=settings_obj.sparse_top_k,
            sparse_overfetch_k=settings_obj.sparse_overfetch_k,
            rerank_top_n=settings_obj.rerank_top_n,
            rerank_score_margin=settings_obj.rerank_score_margin,
            max_distance=settings_obj.max_distance,
            edge_expansion_enabled=settings_obj.edge_expansion_enabled,
            edge_hop_top_k=settings_obj.edge_hop_top_k,
            parent_expansion_enabled=settings_obj.parent_expansion_enabled,
            parent_expansion_min_children=settings_obj.parent_expansion_min_children,
            parent_expansion_max_chars=settings_obj.parent_expansion_max_chars,
            query_planner_model=settings_obj.query_planner_model,
            query_planner_max_subqueries=settings_obj.query_planner_max_subqueries,
            query_decomposition_enabled=settings_obj.query_decomposition_enabled,
            prefer_operative_enabled=settings_obj.prefer_operative_enabled,
            retrieval_operative_only=settings_obj.retrieval_operative_only,
            consolidated_dedup_enabled=settings_obj.consolidated_dedup_enabled,
            sibling_expansion_enabled=settings_obj.sibling_expansion_enabled,
            sibling_expansion_radius=settings_obj.sibling_expansion_radius,
            sibling_expansion_max_chars=settings_obj.sibling_expansion_max_chars,
            sibling_expansion_max_tokens=settings_obj.sibling_expansion_max_tokens,
            subquery_packaging_enabled=settings_obj.subquery_packaging_enabled,
            subquery_reserve_n=settings_obj.subquery_reserve_n,
        )

    def as_trace_dict(self) -> dict:
        data = asdict(self)
        return {
            key: data[key]
            for key in (
                "dense_top_k",
                "sparse_top_k",
                "rerank_top_n",
                "parent_expansion_enabled",
                "prefer_operative_enabled",
                "retrieval_operative_only",
                "consolidated_dedup_enabled",
                "sibling_expansion_enabled",
                "sibling_expansion_radius",
                "sibling_expansion_max_chars",
                "sibling_expansion_max_tokens",
            )
        }


class Strategy(Protocol):
    name: str

    def execute(
        self,
        question: str,
        knobs: RetrievalKnobs | None = None,
        *,
        legal_query: str | None = None,
    ) -> "SelectionResult":
        ...


@dataclass(frozen=True)
class StrategyPreset:
    name: str

    def execute(
        self,
        question: str,
        knobs: RetrievalKnobs | None = None,
        *,
        legal_query: str | None = None,
    ) -> "SelectionResult":
        from app.retriever.context_selection import select_context

        return select_context(
            question,
            knobs=knobs or resolve_knobs(self.name),
            legal_query=legal_query,
        )


_PRESET_KNOBS: dict[str, RetrievalKnobs | None] = {
    "default": None,
    "current_law": RetrievalKnobs(
        dense_top_k=30,
        sparse_top_k=10,
        sparse_overfetch_k=100,
        rerank_top_n=8,
        rerank_score_margin=6.0,
        max_distance=0.5,
        edge_expansion_enabled=True,
        edge_hop_top_k=3,
        parent_expansion_enabled=True,
        parent_expansion_min_children=2,
        parent_expansion_max_chars=8000,
        query_planner_model="mistral",
        query_planner_max_subqueries=3,
        prefer_operative_enabled=True,
        retrieval_operative_only=True,
        consolidated_dedup_enabled=True,
        subquery_packaging_enabled=False,
        subquery_reserve_n=2,
    ),
    "sibling_aware": RetrievalKnobs(
        dense_top_k=30,
        sparse_top_k=10,
        sparse_overfetch_k=100,
        rerank_top_n=8,
        rerank_score_margin=6.0,
        max_distance=0.5,
        edge_expansion_enabled=True,
        edge_hop_top_k=3,
        parent_expansion_enabled=True,
        parent_expansion_min_children=2,
        parent_expansion_max_chars=8000,
        sibling_expansion_enabled=True,
        sibling_expansion_radius=1,
        sibling_expansion_max_chars=3000,
        sibling_expansion_max_tokens=750,
        query_planner_model="mistral",
        query_planner_max_subqueries=3,
        prefer_operative_enabled=False,
        retrieval_operative_only=True,
        consolidated_dedup_enabled=True,
        subquery_packaging_enabled=False,
        subquery_reserve_n=2,
    ),
}

# Candidate stubs kept out of STRATEGIES until R3 trace checks justify a real knob diff.
CANDIDATE_PRESET_STUBS: tuple[str, ...] = ()

STRATEGIES: dict[str, Strategy] = {
    "default": StrategyPreset("default"),
    "current_law": StrategyPreset("current_law"),
    "sibling_aware": StrategyPreset("sibling_aware"),
}


def resolve_knobs(strategy_name: str) -> RetrievalKnobs:
    try:
        preset = _PRESET_KNOBS[strategy_name]
    except KeyError as exc:
        raise ValueError(f"Unknown retrieval strategy: {strategy_name}") from exc
    if preset is None:
        return RetrievalKnobs.from_settings()
    return preset
