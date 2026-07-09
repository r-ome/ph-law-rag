from dataclasses import asdict, dataclass, field

from app.pipeline.policy import AnswerPolicy
from app.retriever.context_selection import SelectionResult
from app.retriever.strategy import RetrievalKnobs, resolve_knobs


@dataclass(frozen=True)
class ModelChoice:
    model: str
    reason: str

    def as_trace_dict(self) -> dict:
        return asdict(self)


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
    router_decision: object | None = None
    router_skipped_reason: str | None = None
    model_choice: ModelChoice | None = None
    prompt: str | None = None
    response: dict | None = None
    policy: AnswerPolicy | None = None

    def __post_init__(self) -> None:
        if self.effective_question is None:
            self.effective_question = self.question
