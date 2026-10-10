"""Generation inference configuration; all provider traffic uses MockTransport."""

import json

import httpx
import pytest
from career_copilot.api import create_app
from career_copilot.config import Settings
from career_copilot.errors import CopilotError, ErrorCode
from career_copilot.models.profile import ProfileFacts
from career_copilot.models.prompts import PROMPTS
from career_copilot.models.resumes import ResumeDraft
from career_copilot.models.router import AI_OBSERVER, AI_PAYLOAD_OBSERVER, ModelRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError


@pytest.mark.parametrize("value", [None, "low", "high", "max"])
def test_reasoning_environment_and_role_isolation(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("GENERATION_REASONING_EFFORT", value)
    settings = Settings(_env_file=None)
    assert settings.for_role("generation").reasoning_effort == value
    assert settings.for_role("judge").reasoning_effort is None
    assert Settings(_env_file=None, judge_provider="jev").for_role("judge").reasoning_effort is None


@pytest.mark.parametrize("value", ["", "LOW", " low", "medium", "none", "minimal", "xhigh", "bad"])
def test_invalid_reasoning_environment_rejected(monkeypatch, value):
    monkeypatch.setenv("GENERATION_REASONING_EFFORT", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("effort", [None, "low"])
def test_settings_reasoning_allowlist_and_privacy(settings, effort):
    settings.generation_reasoning_effort = effort
    settings.generation_base_url = "https://user:password@private.example/v4"
    settings.generation_api_key = SecretStr("private-key")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/settings")
    models = {item["role"]: item for item in response.json()["models"]}
    assert models["generation"]["reasoning_effort"] == effort
    assert "reasoning_effort" not in models["judge"]
    for private in ("private-key", "password", "private.example", "base_url", "raw_payload"):
        assert private not in response.text


@pytest.mark.parametrize(
    "task,schema", [("profile-facts-v1", ProfileFacts), ("resume-generation-v1", ResumeDraft)]
)
@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
@pytest.mark.parametrize("effort", [None, "low", "high", "max"])
def test_exact_wire_body_and_router_config(monkeypatch, settings, task, schema, mode, effort):
    settings = Settings(
        _env_file=None,
        ai_mode="live",
        model="glm-5.3-flash",
        api_key="test-key",
        base_url="https://provider.example/v4",
        generation_reasoning_effort=effort,
        generation_structured_output=mode,
    )
    output = {
        "facts": [
            {
                "type": "other",
                "title": "synthetic",
                "content": "synthetic",
                "source_quote": "synthetic",
            }
        ]
    }
    spec = PROMPTS[task]
    payload = {"synthetic": "contract only"}
    expected_format = {"type": mode}
    if mode == "json_schema":
        expected_format["json_schema"] = {
            "name": schema.__name__,
            "strict": True,
            "schema": schema.model_json_schema(),
        }
    expected = {
        "model": "glm-5.3-flash",
        "messages": [
            {"role": "system", "content": spec.system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": spec.temperature,
        "max_tokens": spec.max_output_tokens,
        "response_format": expected_format,
    }
    if effort is not None:
        expected["reasoning_effort"] = effort
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v4/chat/completions"
        assert json.loads(request.content) == expected
        # Resume returns a schema error after the wire assertion.
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    router = ModelRouter(settings)
    assert router.generation_model.config.reasoning_effort == effort
    assert router.judge_model.config.reasoning_effort is None
    if task == "profile-facts-v1":
        result = router.generation_model.invoke(task, payload, schema)
        assert result.metadata["reasoning_effort"] == effort
    else:
        with pytest.raises(CopilotError):
            router.generation_model.invoke(task, payload, schema)
    assert len(calls) == (1 if task == "profile-facts-v1" else 2)


@pytest.mark.parametrize("outcome", ["success", "timeout", "provider_error", "schema_error"])
def test_trace_reasoning_and_hidden_reasoning_excluded(monkeypatch, settings, outcome):
    settings = Settings(
        _env_file=None,
        ai_mode="live",
        model="glm-5.3-flash",
        api_key="test-key",
        base_url="https://provider.example/v4",
        generation_reasoning_effort="low",
        generation_structured_output="json_object",
        ai_debug_payloads=True,
    )
    output = {
        "facts": [
            {
                "type": "other",
                "title": "synthetic",
                "content": "synthetic",
                "source_quote": "synthetic",
            }
        ]
    }
    marker = "PRIVATE-HIDDEN-REASONING"
    metadata, payloads = [], []

    def handler(request):
        assert json.loads(request.content)["reasoning_effort"] == "low"
        if outcome == "timeout":
            raise httpx.ReadTimeout(marker, request=request)
        if outcome == "provider_error":
            return httpx.Response(503, json={"reasoning": marker})
        content = json.dumps(output) if outcome == "success" else '{"facts": "invalid"}'
        return httpx.Response(
            200,
            json={
                "reasoning": marker,
                "choices": [
                    {
                        "message": {
                            "content": content,
                            "reasoning_content": marker,
                            "thinking": marker,
                            "chain_of_thought": marker,
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "completion_tokens_details": {"reasoning_tokens": 15},
                },
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    observer = AI_OBSERVER.set(metadata.append)
    payload_observer = AI_PAYLOAD_OBSERVER.set(payloads.append)
    try:
        model = ModelRouter(settings).generation_model
        if outcome == "success":
            result = model.invoke("profile-facts-v1", {"raw_input": "synthetic"}, ProfileFacts)
            assert result.output.model_dump() == output
            assert result.metadata["total_tokens"] == 30
        else:
            with pytest.raises(CopilotError) as error:
                model.invoke("profile-facts-v1", {"raw_input": "synthetic"}, ProfileFacts)
            assert error.value.code == (
                ErrorCode.MODEL_OUTPUT_INVALID
                if outcome == "schema_error"
                else ErrorCode.MODEL_REQUEST_FAILED
            )
            assert marker not in str(error.value)
    finally:
        AI_OBSERVER.reset(observer)
        AI_PAYLOAD_OBSERVER.reset(payload_observer)
    assert metadata[0]["reasoning_effort"] == "low"
    if outcome == "timeout":
        assert metadata[0]["provider_metadata"] == {"error_kind": "ReadTimeout"}
    assert marker not in json.dumps([metadata, payloads])
    assert "reasoning_tokens" not in json.dumps(metadata)


def test_fake_metadata_expresses_config(settings):
    settings.generation_reasoning_effort = "low"
    result = ModelRouter(settings).generation_model.invoke(
        "profile-facts-v1", {"raw_input": "synthetic"}, ProfileFacts
    )
    assert result.metadata["reasoning_effort"] == "low"


@pytest.mark.parametrize("outcome", ["success", "timeout", "schema_error"])
def test_persisted_trace_and_http_hide_reasoning(monkeypatch, settings, outcome):
    from career_copilot.database import Database
    from career_copilot.smoke import run_smoke

    settings = Settings(
        _env_file=None,
        ai_mode="live",
        model="glm-5.3-flash",
        api_key="test-key",
        base_url="https://provider.example/v4",
        database_path=settings.database_path,
        generation_reasoning_effort="low",
        generation_structured_output="json_object",
        ai_debug_payloads=True,
    )
    marker = "PRIVATE-HIDDEN-REASONING"

    def handler(request):
        if outcome == "timeout":
            raise httpx.ReadTimeout(marker, request=request)
        content = '{"message": "ok"}' if outcome == "success" else '{"message": 12}'
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content, "reasoning_content": marker}}]},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    if outcome == "success":
        result = run_smoke(settings, "generation", live=True)
        run_id = result["run_id"]
        assert marker not in json.dumps(result)
    else:
        with pytest.raises(CopilotError) as error:
            run_smoke(settings, "generation", live=True)
        run_id = error.value.run_id
    run = Database(settings.database_path).get_run(run_id)
    assert run["nodes"][0]["ai"]["reasoning_effort"] == "low"
    assert marker not in json.dumps(run)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/debug/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["nodes"][0]["ai"]["reasoning_effort"] == "low"
    assert marker not in response.text
