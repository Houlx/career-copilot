import json
from pathlib import Path

import httpx
import pytest
from career_copilot.database import Database
from career_copilot.errors import CopilotError, ErrorCode
from career_copilot.models.prompts import DiagnosticResult
from career_copilot.models.router import ModelRouter, replay_key
from career_copilot.tracing import RunTrace
from pydantic import SecretStr

FIXTURES = Path(__file__).parents[1] / "fixtures" / "replay"


def test_fake_output_is_deterministic_without_http(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Fake must never construct an HTTP client")

    monkeypatch.setattr(httpx, "Client", forbidden)
    model = ModelRouter(settings).judge_model
    first = model.invoke("diagnostic", {}, DiagnosticResult)
    second = model.invoke("diagnostic", {"anything": "ignored"}, DiagnosticResult)
    assert first.output == second.output
    assert first.metadata["total_tokens"] == 0
    assert first.metadata["prompt_version"] == "diagnostic-v1"
    assert first.metadata["model_role"] == "judge"
    assert first.metadata["latency_ms"] >= 0
    with pytest.raises(ValueError):
        ModelRouter(settings).generation_model.invoke("diagnostic", {}, DiagnosticResult)


def test_replay_is_input_and_prompt_version_specific(settings):
    settings.ai_mode = "replay"
    settings.replay_dir = FIXTURES
    model = ModelRouter(settings).judge_model
    assert model.invoke("diagnostic", {}, DiagnosticResult).output.message
    with pytest.raises(CopilotError) as error:
        model.invoke("diagnostic", {"new": "input"}, DiagnosticResult)
    assert error.value.code == ErrorCode.REPLAY_NOT_FOUND
    assert replay_key("t", "v1", {"a": 1, "b": 2}) == replay_key("t", "v1", {"b": 2, "a": 1})
    assert replay_key("t", "v1", {}) != replay_key("t", "v2", {})


@pytest.mark.parametrize(
    "record",
    [
        {"task": "diagnostic", "prompt_version": "wrong", "output": {"message": "ok"}},
        {"task": "diagnostic", "prompt_version": "diagnostic-v1", "output": {"message": 4}},
        {"task": "diagnostic", "prompt_version": "diagnostic-v1"},
    ],
)
def test_replay_rejects_invalid_output(settings, tmp_path, record):
    settings.ai_mode = "replay"
    settings.replay_dir = tmp_path
    (tmp_path / replay_key("diagnostic", "diagnostic-v1", {})).write_text(json.dumps(record))
    with pytest.raises(CopilotError) as error:
        ModelRouter(settings).judge_model.invoke("diagnostic", {}, DiagnosticResult)
    assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID


def test_standalone_replay_does_not_write_runs(client, settings):
    settings.replay_dir = FIXTURES
    response = client.post("/api/debug/replay", json={"task": "diagnostic", "payload": {}})
    assert response.status_code == 200
    assert response.json()["metadata"]["provider"] == "replay"
    assert client.get("/api/debug/runs").json() == {"runs": []}
    assert client.post("/api/debug/replay", json={"task": "profile"}).status_code == 422
    assert client.post("/api/debug/replay", json={"payload": {"missing": 1}}).status_code == 404


def mock_live(settings, monkeypatch, handler):
    # Live transport contract tested with MockTransport only: zero sockets / provider calls.
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    settings.ai_mode = "live"
    settings.model = "test-model"
    settings.api_key = SecretStr("test-secret")
    return ModelRouter(settings).judge_model


def test_mock_live_structured_output_usage_and_node_trace(settings, monkeypatch):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["max_tokens"] == 128
        assert body["response_format"]["type"] == "json_schema"
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"message":"ok"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            },
        )

    model = mock_live(settings, monkeypatch, handler)
    db = Database(settings.database_path)
    db.initialize()
    trace = RunTrace(db, settings, "model-test", "v1")
    db.start_run(
        {
            "run_id": trace.run_id,
            "workflow_name": "model-test",
            "workflow_version": "v1",
            "started_at": "test",
            "status": "running",
        }
    )
    output = trace.node(
        "diagnostic",
        lambda state: {
            "result": model.invoke("diagnostic", {}, DiagnosticResult).output.message,
        },
    )({})
    assert output == {"result": "ok"}
    node = db.get_run(trace.run_id)["nodes"][0]
    assert node["ai"]["total_tokens"] == 17
    assert node["ai"]["input_tokens"] == 12
    assert node["ai"]["output_tokens"] == 5
    assert node["ai"]["model_name"] == "test-model"
    assert node["ai"]["retry_count"] == 0
    assert "test-secret" not in json.dumps(node)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "status,content,expected",
    [
        (503, {}, ErrorCode.MODEL_REQUEST_FAILED),
        (200, {"choices": []}, ErrorCode.MODEL_OUTPUT_INVALID),
        (200, {"choices": [{"message": {"content": "not json"}}]}, ErrorCode.MODEL_OUTPUT_INVALID),
    ],
)
def test_mock_live_retry_is_bounded(settings, monkeypatch, status, content, expected):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(status, json=content)

    model = mock_live(settings, monkeypatch, handler)
    with pytest.raises(CopilotError) as error:
        model.invoke("diagnostic", {}, DiagnosticResult)
    assert error.value.code == expected
    assert len(attempts) == 2


@pytest.mark.parametrize(
    "content", [[], {"usage": {"prompt_tokens": "invalid"}}, {"usage": {"prompt_tokens": -1}}]
)
def test_mock_live_malformed_envelope_classified(settings, monkeypatch, content):
    model = mock_live(settings, monkeypatch, lambda request: httpx.Response(200, json=content))
    with pytest.raises(CopilotError) as error:
        model.invoke("diagnostic", {}, DiagnosticResult)
    assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID


def test_replay_rejects_non_object_envelope(settings, tmp_path):
    settings.ai_mode = "replay"
    settings.replay_dir = tmp_path
    (tmp_path / replay_key("diagnostic", "diagnostic-v1", {})).write_text("[]")
    with pytest.raises(CopilotError) as error:
        ModelRouter(settings).judge_model.invoke("diagnostic", {}, DiagnosticResult)
    assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID


def test_debug_model_payloads_are_explicit_and_redacted(settings, monkeypatch):
    settings.ai_debug_payloads = True
    model = mock_live(
        settings,
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"message":"test-secret"}'}}],
            },
        ),
    )
    db = Database(settings.database_path)
    db.initialize()
    trace = RunTrace(db, settings, "model-test", "v1")
    db.start_run(
        {
            "run_id": trace.run_id,
            "workflow_name": "model-test",
            "workflow_version": "v1",
            "started_at": "test",
            "status": "running",
        }
    )
    trace.node(
        "diagnostic",
        lambda state: {
            "result": model.invoke(
                "diagnostic", {"text": "test-secret"}, DiagnosticResult
            ).output.message,
        },
    )({})
    node = db.get_run(trace.run_id)["nodes"][0]
    assert node["debug_payload"]["model"]["prompt"]
    assert "test-secret" not in json.dumps(node)
    assert node["debug_payload"]["model"]["output"]["message"] == "[REDACTED]"


def test_mock_live_retry_usage_is_cumulative(settings, monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(request)
        output = "invalid json" if len(attempts) == 1 else '{"message":"ok"}'
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": output}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    model = mock_live(settings, monkeypatch, handler)
    result = model.invoke("diagnostic", {}, DiagnosticResult)
    assert result.output.message == "ok"
    assert result.metadata["retry_count"] == 1
    assert result.metadata["total_tokens"] == 30
    assert len(attempts) == 2


def test_standalone_replay_stays_offline_in_live_mode(settings, monkeypatch):
    from career_copilot.api import create_app
    from fastapi.testclient import TestClient

    settings.ai_mode = "live"
    settings.model = "test-model"
    settings.api_key = SecretStr("test-secret")
    settings.replay_dir = FIXTURES

    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must never access live provider")

    with TestClient(create_app(settings)) as client:
        monkeypatch.setattr(httpx, "Client", forbidden)
        response = client.post("/api/debug/replay", json={"payload": {}})
        assert response.status_code == 200
        assert response.json()["metadata"]["provider"] == "replay"
