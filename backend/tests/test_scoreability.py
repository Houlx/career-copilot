"""Responsibility split, native Choice mapping and isolated evaluation regression."""

import json
import runpy
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from career_copilot.config import Settings
from career_copilot.database import Database
from career_copilot.errors import CopilotError, ErrorCode
from career_copilot.models.jobs import (
    JobRequirements,
    ScoreabilityResult,
    apply_classifications,
)
from career_copilot.models.prompts import PROMPTS
from career_copilot.models.providers import SCOREABILITY_CRITERIA
from career_copilot.models.router import AI_OBSERVER, ModelResult, ModelRouter
from career_copilot.tracing import RunTrace

ROOT = Path(__file__).parents[2]
INPUT = {
    "requirements": [
        {"text": "Python", "importance": "required", "category": "skill"},
        {"text": "身体健康", "importance": "required", "category": "personal_trait"},
    ]
}


def config(settings):
    return Settings(
        _env_file=None,
        ai_mode="live",
        judge_provider="jev",
        judge_model="jev-test",
        judge_base_url="https://judge.example/v1",
        judge_api_key="judge-secret",
        generation_model="glm-test",
        generation_base_url="https://generation.example/v1",
        generation_api_key="generation-secret",
        database_path=settings.database_path,
    )


def answer(label):
    return {
        "type": "choice",
        "choice": label,
        "confidence": 1.0,
        "probabilities": {key: float(key == label) for key in SCOREABILITY_CRITERIA},
    }


def response_body():
    return {
        "model": "jev-build",
        "answers": {"0": answer("SCORED"), "1": answer("NON_SCORABLE")},
        "usage": {"input_tokens": 100, "output_tokens": 30},
    }


def mock_http(monkeypatch, body, inspect=None):
    real_client = httpx.Client
    calls = []

    def handle(request):
        calls.append(request)
        if inspect:
            inspect(request)
        return httpx.Response(
            200,
            content=body if isinstance(body, str) else json.dumps(body),
            headers={"x-typesafe-request-id": "request-judge-secret"},
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)
    )
    return calls


@pytest.mark.parametrize(
    "task,role",
    [
        ("profile-facts-v1", "generation"),
        ("profile-normalize-v1", "generation"),
        ("jd-requirements-v2", "generation"),
        ("scoreability-v1", "judge"),
        ("matching-v1", "generation"),
        ("jd-extract-v1", "judge"),
    ],
)
def test_task_roles(task, role):
    assert PROMPTS[task].role == role


def test_native_batch_choice_trace_usage_and_redaction(settings, monkeypatch):
    settings = config(settings)
    settings.ai_debug_payloads = True

    def inspect(request):
        assert str(request.url) == "https://judge.example/v1/systemone"
        assert request.headers["Authorization"] == "Bearer judge-secret"
        body = json.loads(request.content)
        assert body["model"] == "jev-test" and body["state"] == INPUT
        assert set(body["questions"]) == {"0", "1"}
        for i, question in body["questions"].items():
            assert question["type"] == "choice"
            assert question["criteria"] == SCOREABILITY_CRITERIA
            assert f"requirements[{i}]" in question["instructions"]

    body = response_body()
    body.update(authorization="judge-secret", reasoning="private reasoning")
    body["model"] = "jev-build-judge-secret"
    calls = mock_http(monkeypatch, body, inspect)
    db = Database(settings.database_path)
    db.initialize()
    trace = RunTrace(db, settings, "synthetic_scoreability", "scoreability-v1")
    model = ModelRouter(settings).judge_model

    def classify(state):
        return model.invoke("scoreability-v1", state, ScoreabilityResult).model_dump()

    result = trace.execute(
        SimpleNamespace(invoke=trace.node("classify_scoreability", classify)), INPUT
    )
    assert [r["scoreability"] for r in result["output"]["classifications"]] == [
        "SCORED",
        "NON_SCORABLE",
    ]
    run = db.get_run(trace.run_id)
    meta = run["nodes"][0]["ai"]
    assert meta["model_role"] == "judge" and meta["provider"] == "jev"
    assert meta["model_name"] == "jev-test" and meta["prompt_version"] == "scoreability-v1"
    assert (meta["input_tokens"], meta["output_tokens"], meta["total_tokens"]) == (100, 30, 130)
    assert meta["latency_ms"] > 0 and meta["retry_count"] == 0
    assert meta["provider_metadata"]["question_count"] == 2
    assert "judge-secret" not in json.dumps(run) and "private reasoning" not in json.dumps(run)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "bad", ["missing", "extra", "unknown", "criteria", "type", "nan", "sum", "choice", "duplicate"]
)
def test_invalid_native_mapping(settings, monkeypatch, bad):
    body = response_body()
    if bad == "missing":
        del body["answers"]["1"]
    elif bad == "extra":
        body["answers"]["2"] = answer("SCORED")
    elif bad == "unknown":
        body["answers"]["0"]["choice"] = "YES"
    elif bad == "criteria":
        body["answers"]["0"]["probabilities"]["UNKNOWN"] = 0.0
    elif bad == "type":
        body["answers"]["0"]["type"] = "noul"
    elif bad == "nan":
        body["answers"]["0"]["confidence"] = float("nan")
    elif bad == "sum":
        body["answers"]["0"]["probabilities"]["NON_SCORABLE"] = 0.8
    elif bad == "choice":
        body["answers"]["0"]["choice"] = "NON_SCORABLE"
    else:
        body = json.dumps(body).replace(
            '"answers": {', '"answers": {"0": ' + json.dumps(answer("NON_SCORABLE")) + ","
        )
    calls = mock_http(monkeypatch, body)
    observed = []
    token = AI_OBSERVER.set(observed.append)
    try:
        with pytest.raises(CopilotError) as error:
            ModelRouter(config(settings)).judge_model.invoke(
                "scoreability-v1", INPUT, ScoreabilityResult, max_attempts=1
            )
        assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    finally:
        AI_OBSERVER.reset(token)
    assert len(calls) == 1 and observed[0]["latency_ms"] > 0
    if bad != "duplicate":
        assert observed[0]["total_tokens"] == 130


@pytest.mark.parametrize("indices", [[], [0], [0, 0], [0, 2], [0, 1, 2]])
def test_incomplete_business_mapping_rejected(indices):
    with pytest.raises(CopilotError) as error:
        apply_classifications(
            INPUT["requirements"],
            ScoreabilityResult(
                classifications=[
                    {"requirement_index": i, "scoreability": "SCORED"} for i in indices
                ]
            ),
        )
    assert error.value.code == ErrorCode.SCOREABILITY_FAILED


def test_mapping_uses_index_not_answer_order():
    result = ScoreabilityResult(
        classifications=[
            {"requirement_index": 1, "scoreability": "NON_SCORABLE"},
            {"requirement_index": 0, "scoreability": "SCORED"},
        ]
    )
    assert [r["scoreability"] for r in apply_classifications(INPUT["requirements"], result)] == [
        "SCORED",
        "NON_SCORABLE",
    ]


@pytest.mark.parametrize(
    "task,payload,schema",
    [
        ("jd-requirements-v2", {"raw_jd": "Synthetic role 1。要求：Python。"}, JobRequirements),
        ("scoreability-v1", {"requirements": INPUT["requirements"][:1]}, ScoreabilityResult),
    ],
)
@pytest.mark.parametrize("mode", ["fake", "replay"])
def test_new_tasks_offline_no_writes(settings, monkeypatch, task, payload, schema, mode):
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("live HTTP"))
    settings.ai_mode = mode
    role = PROMPTS[task].role
    result = getattr(ModelRouter(settings), f"{role}_model").invoke(task, payload, schema)
    assert result.metadata["provider"] == mode and result.metadata["total_tokens"] == 0
    assert not settings.database_path.exists()


def test_missing_classification_workflow_no_business_write(client, monkeypatch):
    client.post("/api/profile/raw", json={"raw_input": "Synthetic Python", "expected_version": 0})
    client.post("/api/profile/normalize", json={"expected_version": 1})
    client.post("/api/profile/confirm", json={"expected_version": 2})
    monkeypatch.setattr(
        client.app.state.models.judge_model,
        "invoke",
        lambda *a: ModelResult(output=ScoreabilityResult(classifications=[]), metadata={}),
    )
    response = client.post("/api/jobs/analyze", json={"raw_jd": "Synthetic"})
    assert response.json()["error_code"] == "SCOREABILITY_FAILED"
    assert client.get("/api/jobs").json() == {"jobs": []}
    run = client.get("/api/debug/runs/" + response.json()["run_id"]).json()
    assert run["nodes"][-1]["node_name"] == "classify_scoreability"


def script(name):
    return runpy.run_path(str(ROOT / f"scripts/{name}.py"))


@pytest.mark.parametrize(
    "name,args",
    [
        ("eval_profile", ["--live", "--case", "education"]),
        ("eval_jobs", ["--live", "--suite", "scoreability"]),
    ],
)
def test_eval_live_gate_without_ai_live(monkeypatch, name, args):
    monkeypatch.setenv("AI_MODE", "fake")
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("live HTTP"))
    with pytest.raises(SystemExit) as error:
        script(name)["main"](args)
    assert error.value.code == 2


def test_scoreability_eval_batches_eight_cases_and_guard_metrics(settings, monkeypatch, tmp_path):
    module = script("eval_jobs")
    cases = json.loads((ROOT / "evals/scoreability/cases.json").read_text())
    body = response_body()
    body["answers"] = {
        str(i): answer(c["expected"]["requirements"][0]["scoreability"])
        for i, c in enumerate(cases)
    }
    body["answers"]["0"] = answer("SCORED")
    calls = mock_http(monkeypatch, body)

    def no_db(*a, **kw):
        pytest.fail("Evaluation accessed SQLite")

    monkeypatch.setattr(sqlite3, "connect", no_db)
    report = module["run_suite"](config(settings), "scoreability", cases, live=True)
    assert len(calls) == 1 and report["request_count"] == 1
    assert report["raw"]["correct"] == 7 and report["guarded"]["correct"] == 8
    assert report["total_tokens"] == 130 and report["retry_count"] == 0


def test_ambient_live_without_flag_is_replay(settings, monkeypatch, capsys):
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("live HTTP"))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("DB access"))
    script("eval_jobs")["main"](["--suite", "scoreability"])
    assert '"live_api_calls": 0' in capsys.readouterr().out


@pytest.mark.parametrize(
    "task,payload",
    [
        ("jd-requirements-v2", {"raw_jd": "Synthetic role 1。要求：Python。"}),
        ("scoreability-v1", {"requirements": INPUT["requirements"][:1]}),
    ],
)
def test_new_replay_api_no_workflow_or_business_writes(client, monkeypatch, task, payload):
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("live HTTP"))
    before = [client.get(url).json() for url in ("/api/debug/runs", "/api/jobs", "/api/profile")]
    response = client.post("/api/debug/replay", json={"task": task, "payload": payload})
    assert response.status_code == 200
    assert response.json()["metadata"]["provider"] == "replay"
    assert before == [
        client.get(url).json() for url in ("/api/debug/runs", "/api/jobs", "/api/profile")
    ]


@pytest.mark.parametrize(
    "task,payload,schema",
    [
        ("jd-requirements-v2", {"raw_jd": "missing synthetic"}, JobRequirements),
        ("scoreability-v1", INPUT, ScoreabilityResult),
    ],
)
def test_missing_new_replay_never_falls_back_live(settings, monkeypatch, task, payload, schema):
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("live HTTP"))
    router = ModelRouter(config(settings), replay_only=True)
    with pytest.raises(CopilotError) as error:
        getattr(router, f"{PROMPTS[task].role}_model").invoke(task, payload, schema)
    assert error.value.code == ErrorCode.REPLAY_NOT_FOUND


def test_live_eval_failure_records_metadata_no_retry(settings, monkeypatch, tmp_path):
    from career_copilot import business_eval

    # CLI settings are patched without touching environment secrets or actual providers.
    module = script("eval_jobs")
    module["main"].__globals__["Settings"] = lambda **kw: config(settings)
    calls = mock_http(
        monkeypatch,
        {"model": "jev", "answers": {}, "usage": {"input_tokens": 10, "output_tokens": 2}},
    )
    output = tmp_path / "failed.json"
    with pytest.raises(SystemExit) as error:
        module["main"](
            ["--live", "--suite", "scoreability", "--case", "score-0", "--output", str(output)]
        )
    assert error.value.code == 1 and len(calls) == 1
    result = json.loads(output.read_text())
    assert result["request_success"] and not result["schema_mapping_success"]
    assert result["calls"][0]["total_tokens"] == 12
    assert result["calls"][0]["retry_count"] == 0
    assert result["live_api_calls"] == 1
    assert (
        business_eval.failure_report(CopilotError(ErrorCode.MODEL_REQUEST_FAILED), [], live=True)[
            "request_success"
        ]
        is False
    )
