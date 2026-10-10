"""One bounded decision; no autonomous tools, repair loop or final resume writes."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..errors import CopilotError, ErrorCode
from ..models.resumes import ResumeDraft, render_markdown, validate_grounding, validate_language
from ..models.revisions import ResumeRevisionProposal, revision_diff

MAX_AGENT_STEPS = 4


class ResumeRevisionState(TypedDict, total=False):
    thread_id: str
    instruction: dict
    base_resume_id: str
    profile_version: int
    generation: int
    context: dict
    evidence: list[dict]
    agent_step_count: int
    agent_action: str
    proposal: dict
    diff: list[dict]
    markdown: str
    validation_metrics: dict
    language_validation: dict
    proposal_id: str | None


def agent_decide(state, models):
    step = state.get("agent_step_count", 0) + 1
    if step > MAX_AGENT_STEPS:
        raise CopilotError(ErrorCode.REVISION_STEP_LIMIT_EXCEEDED)
    result = models.generation_model.invoke(
        "resume-revision-agent-v1", state["context"], ResumeRevisionProposal
    )
    return {
        "proposal": result.output.model_dump(),
        "agent_step_count": step,
        "agent_action": result.output.action,
    }


def build_revision_graph(trace, revisions, models):
    def load(s):
        return revisions.begin(s["thread_id"], s["instruction"])

    def validate(s):
        proposal = ResumeRevisionProposal.model_validate(s["proposal"])
        draft = ResumeDraft.model_validate(proposal.updated_resume.model_dump())
        try:
            metrics = validate_grounding(draft, s["evidence"], s["profile_version"])
            language = validate_language(draft, s["context"]["current_resume"]["language"])
        except CopilotError as exc:
            raise CopilotError(ErrorCode.REVISION_VALIDATION_FAILED) from exc
        return {
            "validation_metrics": metrics,
            "language_validation": language,
            "markdown": render_markdown(draft),
            "diff": revision_diff(s["context"]["current_resume"], draft.model_dump()),
        }

    def respond(s):
        return {"proposal_id": revisions.finish(s)}

    graph = StateGraph(ResumeRevisionState)
    for name, fn in [
        ("load_revision_context", load),
        ("agent_decide", lambda s: agent_decide(s, models)),
        ("validate_revision", validate),
        ("prepare_revision_response", respond),
    ]:
        graph.add_node(name, trace.node(name, fn, ErrorCode.REVISION_VALIDATION_FAILED))
    graph.add_edge(START, "load_revision_context")
    graph.add_edge("load_revision_context", "agent_decide")
    graph.add_conditional_edges(
        "agent_decide",
        lambda s: s["agent_action"],
        {
            "REVISE": "validate_revision",
            "ASK_USER": "prepare_revision_response",
            "NO_CHANGE": "prepare_revision_response",
        },
    )
    graph.add_edge("validate_revision", "prepare_revision_response")
    graph.add_edge("prepare_revision_response", END)
    return graph.compile()
