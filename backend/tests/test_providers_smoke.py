"""Provider contracts use MockTransport only; pytest blocks real TCP."""

import json
from contextlib import contextmanager

import httpx
import pytest
from career_copilot.api import create_app
from career_copilot.config import Settings
from career_copilot.database import Database
from career_copilot.errors import CopilotError, ErrorCode
from career_copilot.models.jobs import JobExtraction
from career_copilot.models.prompts import DiagnosticResult, JudgeDiagnosticResult
from career_copilot.models.router import AI_OBSERVER, ModelRouter, replay_key
from career_copilot.smoke import main, run_smoke
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.fixture
def live_settings(settings):
    return Settings(
        _env_file=None,
        ai_mode="live",
        database_path=settings.database_path,
        judge_provider="jev",
        judge_model="configured-jev-model",
        judge_base_url="https://judge.example/v1",
        judge_api_key="judge-secret",
        generation_model="configured-glm-model",
        generation_base_url="https://generation.example/v4",
        generation_api_key="generation-secret",
    )


def transport(monkeypatch, handler):
    real_client = httpx.Client
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return clients


def jev_body(**updates):
    return {
        "model": "resolved-jev-build",
        "answers": {"has_python": {"type": "noul", "noul": 0.99}},
        **updates,
    }


def test_judge_smoke_protocol_usage_metadata_and_privacy(live_settings, monkeypatch):
    live_settings.ai_debug_payloads = True
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://judge.example/v1/systemone"
        assert request.headers["Authorization"] == "Bearer judge-secret"
        assert json.loads(request.content) == {
            "model": "configured-jev-model",
            "state": "Synthetic candidate has Python and SQL experience.",
            "questions": {
                "has_python": {
                    "type": "noul",
                    "instructions": "Does the candidate have Python experience?",
                }
            },
        }
        return httpx.Response(
            200,
            headers={"x-typesafe-request-id": "request-123-judge-secret"},
            json=jev_body(
                usage={"input_tokens": 42, "output_tokens": 3},
                authorization="judge-secret",
                reasoning="hidden reasoning must never be stored",
            ),
        )

    clients = transport(monkeypatch, handler)
    result = run_smoke(live_settings, "judge", live=True)
    assert result["output"] == {"decision": True}
    assert len(calls) == 1
    assert clients == [{"timeout": 30, "follow_redirects": False}]
    meta = result["metadata"]
    assert meta["model_role"] == "judge" and meta["provider"] == "jev"
    assert meta["model_name"] == "configured-jev-model"
    assert meta["prompt_version"] == "judge-diagnostic-v1"
    assert meta["latency_ms"] >= 0 and meta["retry_count"] == 0
    assert (meta["input_tokens"], meta["output_tokens"], meta["total_tokens"]) == (42, 3, 45)
    assert meta["provider_metadata"] == {
        "response_model": "resolved-jev-build",
        "probability": 0.99,
        "request_id": "request-123-[REDACTED]",
    }
    db = Database(live_settings.database_path)
    run = db.get_run(result["run_id"])
    assert run["status"] == "completed"
    assert len(run["nodes"]) == 1
    node = run["nodes"][0]
    assert node["debug_payload"]["model"]["output"] == {"decision": True}
    assert node["ai"] == meta
    saved = json.dumps(run)
    assert "judge-secret" not in saved and "hidden reasoning" not in saved
    assert "authorization" not in saved.lower()


def test_generation_smoke_protocol_and_business_isolation(live_settings, monkeypatch):
    db = Database(live_settings.database_path)
    db.initialize()
    # A SQLite authorizer rejects any business-table reads/writes during smoke.
    original_connect = Database.connect

    @contextmanager
    def guarded_connect(self):

        def authorizer(action, table, *args):
            import sqlite3

            if table in {
                "career_profile",
                "career_evidence",
                "jobs",
                "job_requirements",
                "job_matches",
            }:
                if action in {
                    sqlite3.SQLITE_READ,
                    sqlite3.SQLITE_INSERT,
                    sqlite3.SQLITE_UPDATE,
                    sqlite3.SQLITE_DELETE,
                }:
                    pytest.fail(f"Smoke touched business table {table}")
            return sqlite3.SQLITE_OK

        with original_connect(self) as connection:
            connection.set_authorizer(authorizer)
            yield connection

    # initialize() schema DDL is idempotent; only reject data operations.
    monkeypatch.setattr(Database, "connect", guarded_connect)
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        if str(request.url) == "https://judge.example/v1/systemone":
            return httpx.Response(200, json=jev_body())
        assert str(request.url) == "https://generation.example/v4/chat/completions"
        assert request.headers["Authorization"] == "Bearer generation-secret"
        assert body["model"] == "configured-glm-model"
        assert body["response_format"]["type"] == "json_schema"
        assert body["max_tokens"] == 128
        assert "Synthetic" in body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"message":"ok"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    transport(monkeypatch, handler)
    for role in ("judge", "generation"):
        result = run_smoke(live_settings, role, live=True)
        expected = {"decision": True} if role == "judge" else {"message": "ok"}
        assert result["output"] == expected
        assert result["metadata"]["model_role"] == role
        assert result["metadata"]["total_tokens"] == (0 if role == "judge" else 15)
        provider = "jev" if role == "judge" else "openai-compatible"
        assert db.get_run(result["run_id"])["nodes"][0]["ai"]["provider"] == provider
    assert len(calls) == 2


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": None, "output_tokens": None}])
def test_jev_absent_usage_never_estimated(live_settings, monkeypatch, usage):
    transport(monkeypatch, lambda request: httpx.Response(200, json=jev_body(usage=usage)))
    result = ModelRouter(live_settings).judge_model.invoke(
        "judge-diagnostic-v1", {"state": "synthetic"}, JudgeDiagnosticResult
    )
    assert result.metadata["total_tokens"] == 0


@pytest.mark.parametrize("failure", ["timeout", "network", 401, 403, 422, 503])
def test_jev_request_failures_retry_limit(live_settings, monkeypatch, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("judge-secret", request=request)
        if failure == "network":
            raise httpx.ConnectError("judge-secret", request=request)
        return httpx.Response(failure, json={"error": "judge-secret"})

    transport(monkeypatch, handler)
    with pytest.raises(CopilotError) as error:
        ModelRouter(live_settings).judge_model.invoke(
            "judge-diagnostic-v1", {"state": "synthetic"}, JudgeDiagnosticResult
        )
    assert error.value.code == ErrorCode.MODEL_REQUEST_FAILED
    assert "judge-secret" not in str(error.value)
    assert len(calls) == 2


@pytest.mark.parametrize("provider", ["jev", "openai_compatible"])
def test_role_timeouts_reach_http_client(live_settings, monkeypatch, provider):
    live_settings.judge_provider = provider
    live_settings.generation_timeout_seconds = 60
    live_settings.judge_timeout_seconds = 25
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=(
                jev_body()
                if request.url.path.endswith("systemone")
                else {"choices": [{"message": {"content": '{"message":"ok"}'}}]}
            ),
        )

    clients = transport(monkeypatch, handler)
    router = ModelRouter(live_settings)
    for role, task, schema, payload, timeout in (
        ("generation", "generation-diagnostic-v1", DiagnosticResult, {}, 60),
        (
            "judge",
            "judge-diagnostic-v1" if provider == "jev" else "diagnostic",
            JudgeDiagnosticResult if provider == "jev" else DiagnosticResult,
            {"state": "synthetic"},
            25,
        ),
    ):
        result = getattr(router, f"{role}_model").invoke(task, payload, schema, max_attempts=1)
        assert result.metadata["timeout_seconds"] == timeout
    assert clients == [
        {"timeout": 60, "follow_redirects": False},
        {"timeout": 25, "follow_redirects": False},
    ]
    assert [r.extensions["timeout"]["read"] for r in requests] == [60, 25]


def test_generation_read_timeout_keeps_safe_failure_metadata(live_settings, monkeypatch):
    live_settings.generation_timeout_seconds = 60
    requests, metadata = [], []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("private raw exception generation-secret", request=request)

    transport(monkeypatch, handler)
    token = AI_OBSERVER.set(metadata.append)
    try:
        with pytest.raises(CopilotError) as error:
            ModelRouter(live_settings).generation_model.invoke(
                "generation-diagnostic-v1", {}, DiagnosticResult, max_attempts=1
            )
    finally:
        AI_OBSERVER.reset(token)
    assert error.value.code == ErrorCode.MODEL_REQUEST_FAILED
    assert len(requests) == len(metadata) == 1
    assert metadata[0]["timeout_seconds"] == 60
    assert metadata[0]["provider_metadata"] == {"error_kind": "ReadTimeout"}
    assert metadata[0]["retry_count"] == 0
    assert metadata[0]["latency_ms"] >= 0
    assert "private raw exception" not in str(error.value) + json.dumps(metadata)
    assert "generation-secret" not in str(error.value) + json.dumps(metadata)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"answers": {}},
        jev_body(answers={}),
        jev_body(answers={"has_python": {"type": "choice", "choice": "yes"}}),
        jev_body(answers={"has_python": {"type": "noul", "noul": 1.1}}),
        jev_body(answers={"has_python": {"type": "noul", "noul": "0.99"}}),
        jev_body(answers={"has_python": {"type": "noul", "noul": True}}),
        jev_body(usage={"input_tokens": -1}),
    ],
)
def test_jev_invalid_responses(live_settings, monkeypatch, body):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body)

    transport(monkeypatch, handler)
    with pytest.raises(CopilotError) as error:
        ModelRouter(live_settings).judge_model.invoke(
            "judge-diagnostic-v1", {"state": "synthetic"}, JudgeDiagnosticResult
        )
    assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    assert len(calls) == 2


@pytest.mark.parametrize("role", ["judge", "generation"])
def test_failed_smoke_is_one_request_and_traced(live_settings, monkeypatch, role):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "judge-secret generation-secret"})

    transport(monkeypatch, handler)
    with pytest.raises(CopilotError) as error:
        run_smoke(live_settings, role, live=True)
    run = Database(live_settings.database_path).get_run(error.value.run_id)
    assert run["status"] == "failed"
    assert run["nodes"][0]["ai"]["retry_count"] == 0
    assert run["nodes"][0]["ai"]["provider_metadata"]["http_status"] == 401
    assert "judge-secret" not in json.dumps(run)
    assert len(calls) == 1


def test_jev_business_mapping_deferred_without_http(live_settings, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported Jev business tasks must not create a client")

    monkeypatch.setattr(httpx, "Client", forbidden)
    with pytest.raises(CopilotError) as error:
        ModelRouter(live_settings).judge_model.invoke(
            "jd-extract-v1", {"raw_jd": "private input"}, JobExtraction
        )
    assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID


@pytest.mark.parametrize("mode", ["fake", "replay"])
def test_offline_modes_never_construct_adapters(live_settings, monkeypatch, tmp_path, mode):
    from career_copilot.models import router

    def forbidden(*args, **kwargs):
        pytest.fail("Offline mode constructed a live adapter")

    monkeypatch.setattr(router, "JevProvider", forbidden)
    monkeypatch.setattr(router, "OpenAICompatibleProvider", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    live_settings.ai_mode = mode
    live_settings.replay_dir = tmp_path
    for role, schema, output in [
        ("judge", JudgeDiagnosticResult, {"decision": True}),
        ("generation", DiagnosticResult, {"message": "ok"}),
    ]:
        task = f"{role}-diagnostic-v1"
        (tmp_path / replay_key(task, task, {})).write_text(
            json.dumps(
                {
                    "task": task,
                    "prompt_version": task,
                    "output": output,
                }
            )
        )
        result = getattr(ModelRouter(live_settings), f"{role}_model").invoke(task, {}, schema)
        assert result.output.model_dump() == output
        assert result.metadata["provider"] == mode


@pytest.mark.parametrize("mode,flag", [("fake", True), ("replay", True), ("live", False)])
def test_cli_double_gate(live_settings, monkeypatch, mode, flag):
    from career_copilot import smoke

    live_settings.ai_mode = mode
    monkeypatch.setattr(smoke, "Settings", lambda: live_settings)

    def forbidden(*args, **kwargs):
        pytest.fail("Closed gate touched router, DB or HTTP")

    monkeypatch.setattr(smoke, "ModelRouter", forbidden)
    monkeypatch.setattr(smoke, "Database", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    with pytest.raises(SystemExit) as error:
        main(["--role", "judge"] + (["--live"] if flag else []))
    assert error.value.code == 2
    with pytest.raises(ValueError):
        run_smoke(live_settings, "judge", live=flag)


@pytest.mark.parametrize("role", ["judge", "generation"])
def test_cli_open_gate(live_settings, monkeypatch, capsys, role):
    from career_copilot import smoke

    monkeypatch.setattr(smoke, "Settings", lambda: live_settings)
    calls = []

    def handler(request):
        calls.append(request)
        body = (
            jev_body()
            if role == "judge"
            else {"choices": [{"message": {"content": '{"message":"ok"}'}}]}
        )
        return httpx.Response(200, json=body)

    transport(monkeypatch, handler)
    main(["--live", "--role", role])
    assert json.loads(capsys.readouterr().out)["run_id"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field",
    [
        "judge_model",
        "judge_base_url",
        "judge_api_key",
        "generation_model",
        "generation_base_url",
        "generation_api_key",
    ],
)
def test_missing_live_config_rejected(live_settings, field):
    values = live_settings.model_dump()
    values[field] = ""
    # No shared fallback configuration in this test.
    values["base_url"] = ""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_jev_never_falls_back_to_shared_credentials():
    cfg = Settings(
        _env_file=None,
        judge_provider="jev",
        model="shared",
        api_key="shared-secret",
        base_url="https://shared.example/v1",
    ).for_role("judge")
    assert not cfg.api_key.get_secret_value() and not cfg.model and not cfg.base_url


def test_settings_provider_allowlist(live_settings):
    with TestClient(create_app(live_settings)) as client:
        body = client.get("/api/settings").json()
    assert [model["provider"] for model in body["models"]] == ["jev", "openai_compatible"]
    assert "judge-secret" not in json.dumps(body)
    assert "generation-secret" not in json.dumps(body)
    assert "judge.example" not in json.dumps(body)


def test_jev_retry_usage_is_cumulative(live_settings, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json=jev_body(
                answers={} if len(calls) == 1 else {"has_python": {"type": "noul", "noul": 0.9}},
                usage={"input_tokens": 10, "output_tokens": 2},
            ),
        )

    transport(monkeypatch, handler)
    result = ModelRouter(live_settings).judge_model.invoke(
        "judge-diagnostic-v1", {"state": "synthetic"}, JudgeDiagnosticResult
    )
    assert result.metadata["total_tokens"] == 24
    assert result.metadata["retry_count"] == 1
    assert len(calls) == 2


@pytest.mark.parametrize("role", ["judge", "generation"])
def test_wrong_diagnostic_result_fails_without_retry(live_settings, monkeypatch, role):
    calls = []

    def handler(request):
        calls.append(request)
        body = (
            jev_body(answers={"has_python": {"type": "noul", "noul": 0.1}})
            if role == "judge"
            else {"choices": [{"message": {"content": '{"message":"unexpected"}'}}]}
        )
        return httpx.Response(200, json=body)

    transport(monkeypatch, handler)
    with pytest.raises(CopilotError) as error:
        run_smoke(live_settings, role, live=True)
    assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    assert len(calls) == 1
    run = Database(live_settings.database_path).get_run(error.value.run_id)
    assert run["status"] == "failed"


def test_cli_config_failure_is_sanitized(monkeypatch, capsys):
    from career_copilot import smoke

    monkeypatch.setattr(smoke, "Settings", lambda: Settings(_env_file=None))
    monkeypatch.setenv("JUDGE_PROVIDER", "jev")
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("JUDGE_API_KEY", "do-not-print-this-secret")
    with pytest.raises(SystemExit) as error:
        main(["--live", "--role", "judge"])
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "configuration" in stderr
    assert "do-not-print-this-secret" not in stderr
