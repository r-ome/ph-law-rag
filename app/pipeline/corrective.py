from app.pipeline.policy import AnswerPolicy
from app.pipeline.state import AnswerState


def corrective_retrieve(state: AnswerState, policy: AnswerPolicy) -> AnswerState:
    """PR4 contract slot for future curated-corpus corrective retrieval.

    The CRAG implementation will use state.selection plus evidence.missing_facets,
    run at most one targeted retrieval round, merge through the existing dedup path,
    and never search the open web. For PR4 this is intentionally a no-op.
    """
    state.corrective_ran = True
    state.corrective_added_chunks = 0
    return state
