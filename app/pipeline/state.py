from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

from app.pipeline.policy import AnswerPolicy
from app.retriever.context_selection import SelectionResult
from app.retriever.strategy import RetrievalKnobs, resolve_knobs

if TYPE_CHECKING:
    from app.retriever.legal_query_rewriter import LegalRewriteDecision


LegalQuerySeparationArm = Literal[
    "original_only",
    "original_plus_rewrite",
]


@dataclass(frozen=True)
class ModelChoice:
    model: str
    reason: str

    def as_trace_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceReport:
    verdict: Literal["sufficient", "partial", "insufficient"]
    method: Literal["min_chunks", "answerability_gate", "crag_facets"]
    missing_facets: list[str]
    detail: dict

    def as_trace_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "method": self.method,
            "missing_facets": self.missing_facets,
            "detail": self.detail,
        }


@dataclass
class AnswerState:
    question: str
    debug_enabled: bool
    session_id: str | None = None
    effective_question: str | None = None
    query_separation_arm: LegalQuerySeparationArm = "original_only"
    legal_rewrite_decision: LegalRewriteDecision | None = None
    strategy_name: str = "default"
    strategy_knobs: RetrievalKnobs = field(default_factory=lambda: resolve_knobs("default"))
    selection: SelectionResult = field(
        default_factory=lambda: SelectionResult(retrieved=[], pre_expansion=[], selected=[])
    )
    evidence: EvidenceReport | None = None
    corrective_ran: bool = False
    corrective_added_chunks: int = 0
    corrective_baseline_selected_count: int | None = None
    corrective_post_selected_count: int | None = None
    corrective_max_added: int | None = None
    corrective_displaced_baseline_count: int | None = None
    eval_id: str | None = None
    facet_audit_authorize_paid_calls: bool = False
    router_decision: object | None = None
    router_skipped_reason: str | None = None
    model_choice: ModelChoice | None = None
    prompt: str | None = None
    response: dict | None = None
    policy: AnswerPolicy | None = None

    def __post_init__(self) -> None:
        if self.effective_question is None:
            self.effective_question = self.question
