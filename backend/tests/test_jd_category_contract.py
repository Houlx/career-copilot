"""JD prompt/schema alignment and provider boundaries; no real model traffic."""

import json
import re

import httpx
import pytest
from career_copilot.errors import ErrorCode
from career_copilot.models.jobs import ExtractedRequirement, JobRequirements
from career_copilot.models.prompts import PROMPTS
from career_copilot.models.router import ModelRouter
from pydantic import SecretStr, ValidationError

CANONICAL_CATEGORIES = {
    "education",
    "experience",
    "skill",
    "certification",
    "language",
    "capability",
    "logistics",
    "personal_trait",
    "other",
}


def test_prompt_categories_equal_authoritative_schema():
    prompt = PROMPTS["jd-requirements-v2"].system
    category_clause = prompt.split("category: exactly one of ", 1)[1].split(". ", 1)[0]
    prompt_values = re.findall(r'"([a-z_]+)"', category_clause)
    schema_values = ExtractedRequirement.model_json_schema()["properties"]["category"]["enum"]
    assert len(prompt_values) == 9
    assert set(prompt_values) == set(schema_values) == CANONICAL_CATEGORIES
    assert 'importance: exactly one of "required" or "preferred"' in prompt
    assert "Do not invent, translate, rename, create synonyms" in prompt


@pytest.mark.parametrize("category", sorted(CANONICAL_CATEGORIES))
def test_all_canonical_categories_validate(category):
    result = ExtractedRequirement.model_validate_json(
        json.dumps({"text": "Synthetic", "importance": "required", "category": category})
    )
    assert result.category == category


@pytest.mark.parametrize("invalid", [False, True])
def test_mock_provider_json_object_through_job_analysis(client, settings, monkeypatch, invalid):
    # Prepare a confirmed synthetic profile using fake mode only.
    client.post(
        "/api/profile/raw", json={"raw_input": "Synthetic C++ project.", "expected_version": 0}
    ).raise_for_status()
    client.post("/api/profile/normalize", json={"expected_version": 1}).raise_for_status()
    client.post("/api/profile/confirm", json={"expected_version": 2}).raise_for_status()
    output = {
        "title": "Example",
        "requirements": [
            {"text": "硕士及以上学历", "importance": "required", "category": "education"},
            {"text": "熟练使用 C++", "importance": "required", "category": "skill"},
            {"text": "英语能力良好", "importance": "required", "category": "language"},
        ],
    }
    if invalid:
        output["requirements"][1]["category"] = "programming"
        with pytest.raises(ValidationError) as error:
            JobRequirements.model_validate_json(json.dumps(output))
        assert [(e["loc"], e["type"]) for e in error.value.errors()] == [
            (("requirements", 1, "category"), "literal_error")
        ]

    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][0]["content"] == PROMPTS["jd-requirements-v2"].system
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    # Only extraction uses the mocked wire adapter; scoreability and matching stay fake.
    provider_settings = settings.model_copy(
        update={
            "ai_mode": "live",
            "generation_api_key": SecretStr("synthetic-key"),
            "generation_base_url": "https://provider.invalid/v4",
            "generation_structured_output": "json_object",
        }
    )
    extraction_model = ModelRouter(provider_settings).generation_model
    generation = client.app.state.models.generation_model
    original = generation.invoke

    def generate(task, payload, schema):
        if task == "jd-requirements-v2":
            return extraction_model.invoke(task, payload, schema)
        return original(task, payload, schema)

    monkeypatch.setattr(generation, "invoke", generate)
    response = client.post("/api/jobs/analyze", json={"raw_jd": "Synthetic JD"})
    trace = client.get("/api/debug/runs/" + response.json()["run_id"]).json()
    extraction = trace["nodes"][1]
    if invalid:
        assert response.status_code == 500
        assert response.json()["error_code"] == ErrorCode.MODEL_OUTPUT_INVALID.value, response.text
        assert extraction["ai"]["failure_stage"] == "PYDANTIC_VALIDATION_FAILED"
        assert client.get("/api/jobs").json() == {"jobs": []}
        assert len(requests) == 2  # Existing retry policy remains unchanged.
    else:
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        assert job["title"] == "Example"
        assert [r["category"] for r in job["requirements"]] == ["education", "skill", "language"]
        assert [r["text"] for r in job["requirements"]] == [
            r["text"] for r in output["requirements"]
        ]
        assert all(r["scoreability"] == "SCORED" for r in job["requirements"])
        assert trace["status"] == "completed"
        assert "failure_stage" not in extraction["ai"]
        assert len(requests) == 1
