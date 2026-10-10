from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..errors import CopilotError, ErrorCode
from ..models.profile import NormalizedProfile, ProfileFacts, validate_facts, validate_profile
from ..models.router import ModelRouter
from ..profile_store import ProfileStore
from ..tracing import RunTrace


class ProfileState(TypedDict, total=False):
    expected_version: int
    raw_input: str
    facts: dict
    normalized: dict
    facts_valid: bool
    normalized_valid: bool
    saved_version: int


def build_profile_graph(trace: RunTrace, store: ProfileStore, models: ModelRouter):
    def load(state):
        profile = store.get()
        if profile is None:
            raise CopilotError(ErrorCode.PROFILE_NOT_FOUND)
        if profile["profile_version"] != state["expected_version"]:
            raise CopilotError(ErrorCode.PROFILE_CONFLICT)
        return {"raw_input": profile["raw_input"]}

    def extract(state):
        result = models.generation_model.invoke(
            "profile-facts-v1", {"raw_input": state["raw_input"]}, ProfileFacts
        )
        return {"facts": result.output.model_dump()}

    def check_facts(state):
        validate_facts(ProfileFacts.model_validate(state["facts"]), state["raw_input"])
        return {"facts_valid": True}

    def normalize(state):
        result = models.generation_model.invoke(
            "profile-normalize-v1", state["facts"], NormalizedProfile
        )
        return {"normalized": result.output.model_dump()}

    def check_profile(state):
        validate_profile(
            NormalizedProfile.model_validate(state["normalized"]),
            ProfileFacts.model_validate(state["facts"]),
            generated=True,
        )
        return {"normalized_valid": True}

    def persist(state):
        profile = store.persist_draft(
            state["expected_version"],
            state["raw_input"],
            ProfileFacts.model_validate(state["facts"]),
            NormalizedProfile.model_validate(state["normalized"]),
        )
        return {"saved_version": profile["profile_version"]}

    nodes = [
        ("load_raw_profile", load, ErrorCode.PROFILE_NOT_FOUND),
        ("extract_profile_facts", extract, ErrorCode.PROFILE_EXTRACTION_FAILED),
        ("validate_profile_facts", check_facts, ErrorCode.PROFILE_VALIDATION_FAILED),
        ("normalize_profile", normalize, ErrorCode.PROFILE_NORMALIZATION_FAILED),
        ("validate_normalized_profile", check_profile, ErrorCode.PROFILE_VALIDATION_FAILED),
        ("persist_profile_draft", persist, ErrorCode.PERSISTENCE_FAILED),
    ]
    graph = StateGraph(ProfileState)
    previous = START
    for name, fn, failure in nodes:
        graph.add_node(name, trace.node(name, fn, failure))
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()
