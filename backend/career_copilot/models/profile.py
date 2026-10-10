"""Small, strict fact and editable profile contracts; IDs belong to Python."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import CopilotError, ErrorCode
from .diagnostics import diagnostic

EvidenceType = Literal["education", "work", "project", "skill", "other"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Fact(StrictModel):
    type: EvidenceType
    title: str = Field(min_length=1, max_length=300, pattern=r"\S")
    content: str = Field(min_length=1, max_length=20000, pattern=r"\S")
    source_quote: str = Field(min_length=1, max_length=20000, pattern=r"\S")


class ProfileFacts(StrictModel):
    facts: list[Fact] = Field(min_length=1, max_length=200)


class ProfileEntry(StrictModel):
    source_index: int = Field(ge=0)
    type: EvidenceType
    title: str = Field(min_length=1, max_length=300, pattern=r"\S")
    content: str = Field(min_length=1, max_length=20000, pattern=r"\S")


class NormalizedProfile(StrictModel):
    entries: list[ProfileEntry] = Field(min_length=1, max_length=200)


def numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?%?", text))


def validate_facts(facts: ProfileFacts, raw: str):
    for i, fact in enumerate(facts.facts):
        if fact.source_quote not in raw:
            raise CopilotError(
                ErrorCode.PROFILE_VALIDATION_FAILED,
                diagnostics=diagnostic(
                    "BUSINESS_VALIDATION_FAILED",
                    loc=("facts", i, "source_quote"),
                    rule="source_quote_contiguous_excerpt",
                ),
            )
        if not numbers(fact.title + " " + fact.content) <= numbers(fact.source_quote):
            raise CopilotError(
                ErrorCode.PROFILE_VALIDATION_FAILED,
                diagnostics=diagnostic(
                    "BUSINESS_VALIDATION_FAILED",
                    loc=("facts", i, "content"),
                    rule="numeric_grounding",
                ),
            )


def validate_profile(profile: NormalizedProfile, facts: ProfileFacts, *, generated: bool):
    indexes = [entry.source_index for entry in profile.entries]
    if len(indexes) != len(set(indexes)) or any(i >= len(facts.facts) for i in indexes):
        raise CopilotError(
            ErrorCode.PROFILE_VALIDATION_FAILED,
            diagnostics=diagnostic(
                "BUSINESS_VALIDATION_FAILED",
                loc=("entries",),
                rule="source_index_unique_and_in_range",
            ),
        )
    if generated and set(indexes) != set(range(len(facts.facts))):
        raise CopilotError(
            ErrorCode.PROFILE_VALIDATION_FAILED,
            diagnostics=diagnostic(
                "BUSINESS_VALIDATION_FAILED", loc=("entries",), rule="complete_fact_mapping"
            ),
        )
    if generated:
        for i, entry in enumerate(profile.entries):
            fact = facts.facts[entry.source_index]
            if entry.type != fact.type:
                raise CopilotError(
                    ErrorCode.PROFILE_VALIDATION_FAILED,
                    diagnostics=diagnostic(
                        "BUSINESS_VALIDATION_FAILED",
                        loc=("entries", i, "type"),
                        rule="preserve_fact_type",
                    ),
                )
            if numbers(entry.title + " " + entry.content) != numbers(
                fact.title + " " + fact.content
            ):
                raise CopilotError(
                    ErrorCode.PROFILE_VALIDATION_FAILED,
                    diagnostics=diagnostic(
                        "BUSINESS_VALIDATION_FAILED",
                        loc=("entries", i, "content"),
                        rule="preserve_fact_numbers",
                    ),
                )
