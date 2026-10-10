from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..errors import ErrorCode
from ..models.jobs import (
    JobRequirements,
    Matching,
    ScoreabilityResult,
    apply_classifications,
    coverage,
    guard_requirements,
    validate_matches,
)


class JobState(TypedDict, total=False):
    raw_jd: str
    profile_version: int
    evidence: list[dict]
    extraction: dict
    requirements: list[dict]
    requirements_valid: bool
    raw_scoreability: dict
    scoreability_adjusted: bool
    matches: dict
    matches_valid: bool
    coverage: float | None
    job_id: str


def build_job_graph(trace, profiles, jobs, models):
    def load(state):
        profile = profiles.confirmed_profile()
        return {"profile_version": profile["profile_version"], "evidence": profile["evidence"]}

    def extract(state):
        result = models.generation_model.invoke(
            "jd-requirements-v2", {"raw_jd": state["raw_jd"]}, JobRequirements
        )
        return {"extraction": result.output.model_dump()}

    def check_requirements(state):
        extraction = JobRequirements.model_validate(state["extraction"])
        return {
            "requirements": [r.model_dump() for r in extraction.requirements],
            "requirements_valid": True,
        }

    def classify(state):
        if not state["requirements"]:
            return {"requirements": [], "raw_scoreability": {"classifications": []}}
        result = models.judge_model.invoke(
            "scoreability-v1", {"requirements": state["requirements"]}, ScoreabilityResult
        )
        return {
            "requirements": apply_classifications(state["requirements"], result.output),
            "raw_scoreability": result.output.model_dump(),
        }

    def guard(state):
        requirements, adjusted = guard_requirements(state["requirements"])
        return {"requirements": requirements, "scoreability_adjusted": adjusted}

    def match(state):
        requirements = [
            {**r, "requirement_index": i}
            for i, r in enumerate(state["requirements"])
            if r["scoreability"] == "SCORED"
        ]
        if not requirements:
            return {"matches": {"matches": []}}
        evidence = [
            {k: e[k] for k in ("id", "type", "title", "normalized_content")}
            for e in state["evidence"]
        ]
        result = models.generation_model.invoke(
            "matching-v1", {"requirements": requirements, "evidence": evidence}, Matching
        )
        return {"matches": result.output.model_dump()}

    def check_matches(state):
        validate_matches(
            state["requirements"],
            Matching.model_validate(state["matches"]),
            state["evidence"],
            state["profile_version"],
        )
        return {"matches_valid": True}

    def calculate(state):
        return {
            "coverage": coverage(state["requirements"], Matching.model_validate(state["matches"]))
        }

    def persist(state):
        return {"job_id": jobs.persist(state)}

    nodes = [
        ("load_confirmed_profile", load, ErrorCode.PROFILE_VALIDATION_FAILED),
        ("extract_job_requirements", extract, ErrorCode.JD_EXTRACTION_FAILED),
        ("validate_requirements", check_requirements, ErrorCode.MODEL_OUTPUT_INVALID),
        ("classify_scoreability", classify, ErrorCode.SCOREABILITY_FAILED),
        ("apply_scoreability_guard", guard, ErrorCode.SCOREABILITY_FAILED),
        ("match_requirements", match, ErrorCode.MATCHING_FAILED),
        ("validate_matches", check_matches, ErrorCode.MATCH_VALIDATION_FAILED),
        ("calculate_coverage", calculate, ErrorCode.MATCH_VALIDATION_FAILED),
        ("persist_job_analysis", persist, ErrorCode.PERSISTENCE_FAILED),
    ]
    graph = StateGraph(JobState)
    previous = START
    for name, fn, failure in nodes:
        graph.add_node(name, trace.node(name, fn, failure))
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()
