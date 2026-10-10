"""Strict model contracts and deterministic job analysis rules."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import CopilotError, ErrorCode


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Scoreability = Literal["SCORED", "NEEDS_CONFIRMATION", "NON_SCORABLE"]


class ExtractedRequirement(Strict):
    text: str = Field(min_length=1, max_length=4000)
    importance: Literal["required", "preferred"]
    category: Literal[
        "education",
        "experience",
        "skill",
        "certification",
        "language",
        "capability",
        "logistics",
        "personal_trait",
        "other",
    ]


class Requirement(ExtractedRequirement):
    scoreability: Scoreability


class JobExtraction(Strict):
    title: str = Field(min_length=1, max_length=300)
    requirements: list[Requirement] = Field(max_length=100)


class JobRequirements(Strict):
    title: str = Field(min_length=1, max_length=300)
    requirements: list[ExtractedRequirement] = Field(max_length=100)


class ScoreabilityInput(Strict):
    requirements: list[ExtractedRequirement] = Field(max_length=100)


class ScoreabilityAnswer(Strict):
    requirement_index: int = Field(ge=0)
    scoreability: Scoreability


class ScoreabilityResult(Strict):
    classifications: list[ScoreabilityAnswer] = Field(max_length=100)


def apply_classifications(requirements: list[dict], result: ScoreabilityResult) -> list[dict]:
    indices = [answer.requirement_index for answer in result.classifications]
    if len(indices) != len(requirements) or set(indices) != set(range(len(requirements))):
        raise CopilotError(ErrorCode.SCOREABILITY_FAILED)
    labels = {answer.requirement_index: answer.scoreability for answer in result.classifications}
    return [
        Requirement.model_validate({**r, "scoreability": labels[i]}).model_dump()
        for i, r in enumerate(requirements)
    ]


def fake_scoreability(payload: dict) -> dict:
    # Synthetic demo only. Keep this separate from the unchanged production safety guard.
    requirements = ScoreabilityInput.model_validate(payload).requirements
    return {
        "classifications": [
            {
                "requirement_index": i,
                "scoreability": (
                    "NON_SCORABLE"
                    if r.category == "personal_trait"
                    else "NEEDS_CONFIRMATION"
                    if r.category == "logistics"
                    else "SCORED"
                ),
            }
            for i, r in enumerate(requirements)
        ]
    }


class Match(Strict):
    requirement_index: int = Field(ge=0)
    status: Literal["MATCH", "PARTIAL", "GAP"]
    evidence_ids: list[str] = Field(max_length=500)
    reason: str = Field(min_length=1, max_length=4000)


class Matching(Strict):
    matches: list[Match] = Field(max_length=100)


def guard_requirements(requirements: list[dict]) -> tuple[list[dict], bool]:
    guarded, adjusted = [], False
    for requirement in requirements:
        r = dict(requirement)
        if r["scoreability"] == "SCORED" and any(
            word in r["text"] for word in ("身体健康", "爱岗敬业", "吃苦耐劳", "道德品质")
        ):
            r["scoreability"] = "NON_SCORABLE"
            adjusted = True
        guarded.append(r)
    return guarded, adjusted


def validate_matches(
    requirements: list[dict], matches: Matching, evidence: list[dict], version: int
):
    expected = {i for i, r in enumerate(requirements) if r["scoreability"] == "SCORED"}
    actual = [m.requirement_index for m in matches.matches]
    valid_ids = {e["id"] for e in evidence if e["active"] and e["profile_version"] == version}
    if set(actual) != expected or len(actual) != len(expected):
        raise CopilotError(ErrorCode.MATCH_VALIDATION_FAILED)
    for m in matches.matches:
        if (m.status != "GAP" and not m.evidence_ids) or not set(m.evidence_ids) <= valid_ids:
            raise CopilotError(ErrorCode.MATCH_VALIDATION_FAILED)


def coverage(requirements: list[dict], matches: Matching) -> float | None:
    weights = {"required": 2, "preferred": 1}
    values = {"MATCH": 1, "PARTIAL": 0.5, "GAP": 0}
    scored = {
        i: weights[r["importance"]]
        for i, r in enumerate(requirements)
        if r["scoreability"] == "SCORED"
    }
    if not scored:
        return None
    return round(
        100
        * sum(scored[m.requirement_index] * values[m.status] for m in matches.matches)
        / sum(scored.values()),
        1,
    )


def fake_matching(payload: dict) -> dict:
    """Literal synthetic demo only; no claim of semantic matching quality."""
    results = []
    for r in payload["requirements"]:
        text = r["text"].casefold()
        exact = [e["id"] for e in payload["evidence"] if text in e["normalized_content"].casefold()]
        parts = [part.strip() for part in text.replace(" and ", "/").split("/") if part.strip()]
        related = [
            e["id"]
            for e in payload["evidence"]
            if any(part in e["normalized_content"].casefold() for part in parts)
        ]
        results.append(
            {
                "requirement_index": r["requirement_index"],
                "status": "MATCH" if exact else "PARTIAL" if related else "GAP",
                "evidence_ids": exact or related,
                "reason": "Synthetic literal evidence check; not a semantic quality estimate.",
            }
        )
    return {"matches": results}
