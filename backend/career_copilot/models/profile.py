"""Small, strict fact and editable profile contracts; IDs belong to Python."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import CopilotError, ErrorCode

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
    for fact in facts.facts:
        if fact.source_quote not in raw or not numbers(fact.title + " " + fact.content) <= numbers(
            fact.source_quote
        ):
            raise CopilotError(ErrorCode.PROFILE_VALIDATION_FAILED)


def validate_profile(profile: NormalizedProfile, facts: ProfileFacts, *, generated: bool):
    indexes = [entry.source_index for entry in profile.entries]
    if len(indexes) != len(set(indexes)) or any(i >= len(facts.facts) for i in indexes):
        raise CopilotError(ErrorCode.PROFILE_VALIDATION_FAILED)
    if generated and set(indexes) != set(range(len(facts.facts))):
        raise CopilotError(ErrorCode.PROFILE_VALIDATION_FAILED)
    if generated:
        for entry in profile.entries:
            fact = facts.facts[entry.source_index]
            if entry.type != fact.type or numbers(entry.title + " " + entry.content) != numbers(
                fact.title + " " + fact.content
            ):
                raise CopilotError(ErrorCode.PROFILE_VALIDATION_FAILED)
