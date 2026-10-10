from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..errors import ErrorCode
from ..models.resumes import ResumeDraft, render_markdown, validate_grounding, validate_language


class ResumeState(TypedDict, total=False):
    job_id: str
    language: str
    profile_version: int
    context: dict
    evidence: list[dict]
    draft: dict
    validation_metrics: dict
    language_validation: dict
    markdown: str
    resume_id: str


def build_resume_graph(trace, resumes, models):
    def load(s):
        return resumes.context(s["job_id"], s["language"])

    def generate(s):
        result = models.generation_model.invoke("resume-generation-v2", s["context"], ResumeDraft)
        return {"draft": result.output.model_dump()}

    def schema(s):
        return {"draft": ResumeDraft.model_validate(s["draft"]).model_dump()}

    def grounding(s):
        return {
            "validation_metrics": validate_grounding(
                ResumeDraft.model_validate(s["draft"]), s["evidence"], s["profile_version"]
            )
        }

    def language(s):
        return {
            "language_validation": validate_language(
                ResumeDraft.model_validate(s["draft"]), s["language"]
            )
        }

    def render(s):
        return {"markdown": render_markdown(ResumeDraft.model_validate(s["draft"]))}

    def persist(s):
        return {"resume_id": resumes.persist(s)}

    graph = StateGraph(ResumeState)
    previous = START
    for name, fn, error in [
        ("load_resume_context", load, ErrorCode.RESUME_VALIDATION_FAILED),
        ("generate_resume", generate, ErrorCode.RESUME_GENERATION_FAILED),
        ("validate_resume_schema", schema, ErrorCode.MODEL_OUTPUT_INVALID),
        ("validate_resume_grounding", grounding, ErrorCode.RESUME_GROUNDING_FAILED),
        ("validate_resume_language", language, ErrorCode.RESUME_VALIDATION_FAILED),
        ("render_resume_markdown", render, ErrorCode.RESUME_VALIDATION_FAILED),
        ("persist_resume", persist, ErrorCode.PERSISTENCE_FAILED),
    ]:
        graph.add_node(name, trace.node(name, fn, error))
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()
