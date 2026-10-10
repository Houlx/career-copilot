"""Generation inference configuration; all provider traffic uses MockTransport."""

import json

import httpx
import pytest
from career_copilot.api import create_app
from career_copilot.config import Settings
from career_copilot.errors import CopilotError, ErrorCode
from career_copilot.models.jobs import JobRequirements, Matching
from career_copilot.models.profile import NormalizedProfile, ProfileFacts
from career_copilot.models.prompts import PROMPTS, DiagnosticResult
from career_copilot.models.resumes import ResumeDraft
from career_copilot.models.revisions import ResumeRevisionProposal
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
    assert response.json()["task_policies"] == {
        task: {"reasoning_effort": spec.reasoning_effort}
        for task, spec in PROMPTS.items()
        if spec.role == "generation" and spec.reasoning_effort is not None
    }
    for spec in PROMPTS.values():
        assert spec.system not in response.text
    for private in ("private-key", "password", "private.example", "base_url", "raw_payload"):
        assert private not in response.text


@pytest.mark.parametrize(
    "task,schema",
    [
        ("profile-facts-v1", ProfileFacts),
        ("profile-normalize-v1", NormalizedProfile),
        ("jd-requirements-v2", JobRequirements),
        ("matching-v1", Matching),
        ("resume-generation-v2", ResumeDraft),
        ("resume-revision-agent-v1", ResumeRevisionProposal),
        ("generation-diagnostic-v1", DiagnosticResult),
    ],
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
            {"role": "system", "content": spec.runtime_system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": spec.temperature,
        "max_tokens": spec.max_output_tokens,
        "response_format": expected_format,
    }
    effective = PROMPTS[task].reasoning_effort or effort
    source = "task" if PROMPTS[task].reasoning_effort else "role" if effort else "provider_default"
    if effective is not None:
        expected["reasoning_effort"] = effective
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v4/chat/completions"
        assert json.loads(request.content) == expected
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 30
        )
        # Resume returns a schema error after the wire assertion.
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    router = ModelRouter(settings)
    assert router.generation_model.config.reasoning_effort == effort
    assert router.judge_model.config.reasoning_effort is None
    metadata = []
    token = AI_OBSERVER.set(metadata.append)
    try:
        if task == "profile-facts-v1":
            result = router.generation_model.invoke(task, payload, schema)
            assert result.metadata["reasoning_effort"] == effective
        else:
            with pytest.raises(CopilotError):
                router.generation_model.invoke(task, payload, schema)
    finally:
        AI_OBSERVER.reset(token)
    assert metadata[0]["reasoning_effort"] == effective
    assert metadata[0]["reasoning_source"] == source
    assert router.generation_model.config.reasoning_effort == effort
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
    assert metadata[0]["reasoning_source"] == "task"
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
    assert result.metadata["reasoning_source"] == "task"


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
    assert run["nodes"][0]["ai"]["reasoning_source"] == "role"
    assert marker not in json.dumps(run)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/debug/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["nodes"][0]["ai"]["reasoning_effort"] == "low"
    assert marker not in response.text


def test_existing_task_overrides_preserved():
    assert {
        task: spec.reasoning_effort
        for task, spec in PROMPTS.items()
        if spec.reasoning_effort is not None
    } == {
        "profile-facts-v1": "low",
        "jd-extract-v1": "low",
        "jd-requirements-v2": "high",
        "matching-v1": "high",
        "resume-generation-v2": "high",
        "resume-revision-agent-v1": "high",
    }


@pytest.mark.parametrize("mode", ["fake", "replay", "live"])
@pytest.mark.parametrize("effort", [None, "low", "high"])
@pytest.mark.parametrize("task", ["profile-facts-v1", "resume-generation-v2"])
@pytest.mark.parametrize("failure", [False, True])
def test_effective_policy_persisted_for_every_mode(
    monkeypatch, settings, mode, effort, task, failure
):
    from pathlib import Path
    from types import SimpleNamespace

    from career_copilot.database import Database
    from career_copilot.tracing import RunTrace

    # Reuse existing synthetic fixtures, keeping the canonical business input unchanged.
    if task == "profile-facts-v1":
        payload = {"raw_input": "使用 Python。"}
        schema = ProfileFacts
    else:
        case = json.loads(Path("evals/resumes/cases.json").read_text())[0]
        payload = case["payload"]
        schema = ResumeDraft
    settings.ai_mode = mode
    settings.model = "glm-5.3-flash"
    settings.api_key = SecretStr("test-key")
    settings.base_url = "https://provider.example/v4"
    settings.generation_reasoning_effort = effort
    if failure and mode == "replay":
        settings.replay_dir = settings.database_path.parent / "missing-fixtures"
    if failure and mode == "fake":
        payload = {}
    effective = PROMPTS[task].reasoning_effort or effort
    source = "task" if PROMPTS[task].reasoning_effort else "role" if effort else "provider_default"
    if mode == "live":
        # Output comes from a local fake call, HTTP always intercepted.
        fake_settings = settings.model_copy(update={"ai_mode": "fake"})
        output = ModelRouter(fake_settings).generation_model.invoke(task, payload, schema).output

        def handler(request):
            body = json.loads(request.content)
            assert body.get("reasoning_effort") == effective
            assert ("reasoning_effort" in body) == (effective is not None)
            if failure:
                raise httpx.ReadTimeout("synthetic", request=request)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": output.model_dump_json()}}]}
            )

        real_client = httpx.Client
        monkeypatch.setattr(
            httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
        )
    db = Database(settings.database_path)
    db.initialize()
    trace = RunTrace(db, settings, "policy_test", task)
    model = ModelRouter(settings).generation_model
    node = trace.node("generate", lambda state: model.invoke(task, state, schema).model_dump())
    if failure:
        with pytest.raises(CopilotError):
            trace.execute(SimpleNamespace(invoke=node), payload)
    else:
        trace.execute(SimpleNamespace(invoke=node), payload)
    run = db.get_run(trace.run_id)
    assert run["status"] == ("failed" if failure else "completed")
    meta = run["nodes"][0]["ai"]
    assert meta["reasoning_effort"] == effective
    assert meta["reasoning_source"] == source
    if mode != "live":
        assert meta["input_tokens"] == meta["output_tokens"] == meta["total_tokens"] == 0
