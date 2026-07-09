from dataclasses import asdict, dataclass, field
from typing import Literal

from app.pipeline.policy import AnswerPolicy
from app.retriever.context_selection import SelectionResult
from app.retriever.strategy import RetrievalKnobs, resolve_knobs


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
    strategy_name: str = "default"
    strategy_knobs: RetrievalKnobs = field(default_factory=lambda: resolve_knobs("default"))
    selection: SelectionResult = field(
        default_factory=lambda: SelectionResult(retrieved=[], pre_expansion=[], selected=[])
    )
    evidence: EvidenceReport | None = None
    corrective_ran: bool = False
    corrective_added_chunks: int = 0
    router_decision: object | None = None
    router_skipped_reason: str | None = None
    model_choice: ModelChoice | None = None
    prompt: str | None = None
    response: dict | None = None
    policy: AnswerPolicy | None = None

    def __post_init__(self) -> None:
        if self.effective_question is None:
            self.effective_question = self.question
