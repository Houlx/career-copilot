"""Pydantic is the sole structural prompt contract; all verification is offline."""

import json
from dataclasses import replace
from typing import Literal

import pytest
from career_copilot.models.contracts import (
    build_prompt_contract,
    contract_hash,
    serialize_contract,
)
from career_copilot.models.jobs import JobRequirements, Matching
from career_copilot.models.profile import NormalizedProfile, ProfileFacts
from career_copilot.models.prompts import PROMPTS, PromptSpec
from career_copilot.models.resumes import ResumeDraft
from career_copilot.models.revisions import ResumeRevisionProposal
from career_copilot.models.router import AI_OBSERVER, AI_PAYLOAD_OBSERVER, ModelRouter, replay_key
from pydantic import BaseModel, ConfigDict, Field


def test_enum_changes_flow_from_model():
    class Example(BaseModel):
        status: Literal["A", "B"]

    class Extended(BaseModel):
        status: Literal["A", "B", "C"]

    first = build_prompt_contract(Example)
    second = build_prompt_contract(Extended)
    assert first["properties"]["status"]["enum"] == ["A", "B"]
    assert second["properties"]["status"]["enum"] == ["A", "B", "C"]
    assert contract_hash(first) != contract_hash(second)
    assert serialize_contract(first) == serialize_contract(build_prompt_contract(Example))
    assert contract_hash(first) == contract_hash(dict(reversed(list(first.items()))))


def test_matching_contract():
    contract = build_prompt_contract(Matching)
    assert set(contract["properties"]) == {"matches"}
    match = contract["properties"]["matches"]["items"]
    assert (
        set(match["properties"])
        == set(match["required"])
        == {"requirement_index", "status", "evidence_ids", "reason"}
    )
    assert match["additionalProperties"] is False
    fields = match["properties"]
    assert fields["status"]["enum"] == ["MATCH", "PARTIAL", "GAP"]
    assert fields["requirement_index"]["minimum"] == 0
    assert fields["reason"] == {"type": "string", "minLength": 1, "maxLength": 4000}
    assert fields["evidence_ids"] == {"type": "array", "items": {"type": "string"}, "maxItems": 500}
    assert {"results", "result", "explanation"}.isdisjoint(match["properties"])


@pytest.mark.parametrize(
    "task,model",
    [
        ("profile-facts-v1", ProfileFacts),
        ("profile-normalize-v1", NormalizedProfile),
        ("jd-requirements-v2", JobRequirements),
        ("matching-v1", Matching),
        ("resume-generation-v2", ResumeDraft),
        ("resume-revision-agent-v1", ResumeRevisionProposal),
    ],
)
def test_all_structured_tasks_bound_and_injected(task, model):
    spec = PROMPTS[task]
    assert spec.response_model is model
    contract = build_prompt_contract(model)
    assert contract["additionalProperties"] is False
    assert spec.runtime_system.startswith(spec.system + "\n\nReturn JSON only.")
    assert serialize_contract(contract) in spec.runtime_system
    assert "$ref" not in serialize_contract(contract)
    assert "$defs" not in serialize_contract(contract)


def test_resume_nested_constraints():
    contract = build_prompt_contract(ResumeDraft)
    section = contract["properties"]["sections"]["items"]
    assert set(section["required"]) == {"kind", "items"}
    assert section["properties"]["kind"]["enum"] == [
        "skills",
        "experience",
        "projects",
        "education",
        "additional",
    ]
    item = section["properties"]["items"]["items"]
    assert item == contract["properties"]["headline"]
    assert item["additionalProperties"] is False
    assert item["properties"]["text"]["pattern"] == r"\S"
    assert item["properties"]["evidence_ids"]["minItems"] == 1
    assert item["properties"]["evidence_ids"]["items"]["pattern"].startswith("^career-")


def test_metadata_removed_and_required_unchanged():
    class Example(BaseModel):
        model_config = ConfigDict(extra="forbid")
        title: str = Field(min_length=1, description="Metadata", examples=["example"])
        count: int = Field(default=1, ge=0, le=5)

    contract = build_prompt_contract(Example)
    assert contract == {
        "type": "object",
        "additionalProperties": False,
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "count": {"type": "integer", "minimum": 0, "maximum": 5},
        },
    }


def test_unstructured_prompt_unchanged():
    spec = PromptSpec(version="text-v1", role="generation", system="Business", max_output_tokens=10)
    assert spec.response_model is None
    assert spec.runtime_system == "Business"


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://invalid.example/schema"},
        {"$ref": "#/$defs/Missing"},
        {"$defs": {"Loop": {"$ref": "#/$defs/Loop"}}, "$ref": "#/$defs/Loop"},
        {"oneOf": [{"type": "string"}, {"type": "null"}]},
        {"type": "object", "unsupportedConstraint": True},
        {"$defs": {"X": {"type": "string"}}, "$ref": "#/$defs/X", "minLength": 2},
    ],
)
def test_unsupported_schema_fails_loudly(schema):
    class Example(BaseModel):
        @classmethod
        def model_json_schema(cls):
            return schema

    with pytest.raises(ValueError):
        build_prompt_contract(Example)


@pytest.mark.parametrize("mode", ["fake", "replay"])
def test_contract_metadata_and_debug_prompt(settings, mode):
    settings.ai_mode = mode
    model = ModelRouter(settings).generation_model
    metadata, payloads = [], []
    token = AI_OBSERVER.set(metadata.append)
    payload_token = AI_PAYLOAD_OBSERVER.set(payloads.append)
    try:
        result = model.invoke("profile-facts-v1", {"raw_input": "使用 Python。"}, ProfileFacts)
    finally:
        AI_OBSERVER.reset(token)
        AI_PAYLOAD_OBSERVER.reset(payload_token)
    expected = contract_hash(build_prompt_contract(ProfileFacts))
    assert result.metadata["schema_contract_hash"] == expected
    assert metadata[0]["schema_contract_hash"] == expected
    assert payloads[0]["prompt"] == PROMPTS["profile-facts-v1"].runtime_system
    assert "properties" not in json.dumps(metadata)
    assert result.metadata["total_tokens"] == 0


def test_response_model_mismatch_rejected(settings):
    with pytest.raises(ValueError, match="response model"):
        ModelRouter(settings).generation_model.invoke("matching-v1", {}, ProfileFacts)


def test_replay_key_independent_of_runtime_prompt():
    spec = PROMPTS["matching-v1"]
    changed = replace(spec, system="Changed business prompt")
    assert spec.runtime_system != changed.runtime_system
    assert replay_key("matching-v1", spec.version, {}) == replay_key(
        "matching-v1", changed.version, {}
    )


def test_revision_nullable_contract_retains_nested_constraints():
    contract = build_prompt_contract(ResumeRevisionProposal)
    branches = contract["properties"]["updated_resume"]["anyOf"]
    assert branches[1] == {"type": "null"}
    assert branches[0]["additionalProperties"] is False
    assert branches[0]["properties"]["headline"]["properties"]["evidence_ids"]["minItems"] == 1
    assert contract["properties"]["action"]["enum"] == ["REVISE", "ASK_USER", "NO_CHANGE"]
    assert "$ref" not in serialize_contract(contract)
