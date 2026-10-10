import json
from pathlib import Path

import httpx
import pytest
from career_copilot.api import create_app
from career_copilot.errors import CopilotError
from career_copilot.job_eval import extraction_metrics, matching_metrics, scoreability_metrics
from career_copilot.models.jobs import (
    JobExtraction,
    JobRequirements,
    Matching,
    ScoreabilityResult,
    coverage,
    guard_requirements,
    validate_matches,
)
from career_copilot.models.router import ModelResult
from fastapi.testclient import TestClient
from pydantic import ValidationError


def confirmed(client):
    client.post(
        "/api/profile/raw", json={"raw_input": "Synthetic Python project.", "expected_version": 0}
    ).raise_for_status()
    client.post("/api/profile/normalize", json={"expected_version": 1}).raise_for_status()
    return client.post("/api/profile/confirm", json={"expected_version": 2}).json()["profile"]


def analyze(client):
    return client.post("/api/jobs/analyze", json={"raw_jd": "Synthetic developer JD"})


def requirement(**changes):
    return (
        dict(
            text="Python", importance="required", category="skill", scoreability="SCORED", **changes
        )
        if not changes
        else {**requirement(), **changes}
    )


def match(status="MATCH", ids=None, index=0):
    return dict(
        requirement_index=index,
        status=status,
        evidence_ids=["e1"] if ids is None else ids,
        reason="Synthetic reference",
    )


def test_full_flow_restart_outdated_delete(client, settings, monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("live client constructed"))
    profile = confirmed(client)
    response = analyze(client)
    assert response.status_code == 200, response.text
    result = response.json()
    job = result["job"]
    assert job["coverage"] == 66.7 and job["profile_version"] == 3 and not job["outdated"]
    assert len(job["requirements"]) == 4
    assert [r["id"] for r in job["requirements"]] == ["req-001", "req-002", "req-003", "req-004"]
    assert job["requirements"][0]["match"]["evidence"][0]["id"] == profile["evidence"][0]["id"]
    assert job["requirements"][1]["match"]["status"] == "GAP"
    assert job["requirements"][2]["match"] is None
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/jobs/" + job["id"]).json()["job"] == job
    trace = client.get("/api/debug/runs/" + result["run_id"]).json()
    assert trace["workflow_name"] == "job_analysis_workflow"
    assert [n["node_name"] for n in trace["nodes"]] == [
        "load_confirmed_profile",
        "extract_job_requirements",
        "validate_requirements",
        "classify_scoreability",
        "apply_scoreability_guard",
        "match_requirements",
        "validate_matches",
        "calculate_coverage",
        "persist_job_analysis",
    ]
    for index, role, prompt in [
        (1, "generation", "jd-requirements-v2"),
        (3, "judge", "scoreability-v1"),
        (5, "generation", "matching-v1"),
    ]:
        meta = trace["nodes"][index]["ai"]
        assert meta["model_role"] == role and meta["prompt_version"] == prompt
        assert meta["provider"] == "fake" and meta["total_tokens"] == 0
        assert meta["latency_ms"] >= 0 and meta["retry_count"] == 0
    assert trace["nodes"][7]["state_changes"]["coverage"]["after"] == 66.7
    assert all("debug_payload" not in n for n in trace["nodes"])
    client.post(
        "/api/profile/raw", json={"raw_input": "Changed synthetic profile.", "expected_version": 3}
    )
    assert client.get("/api/jobs").json()["jobs"][0]["outdated"]
    assert analyze(client).status_code == 422
    client.post("/api/profile/normalize", json={"expected_version": 4})
    client.post("/api/profile/confirm", json={"expected_version": 5})
    old = client.get("/api/jobs/" + job["id"]).json()["job"]
    assert old["outdated"] and old["requirements"][0]["match"]["evidence"]
    assert client.delete("/api/jobs/" + job["id"]).status_code == 200
    assert client.get("/api/jobs/" + job["id"]).status_code == 404
    with client.app.state.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM job_matches").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM job_requirements").fetchone()[0] == 0


@pytest.mark.parametrize("saved", [False, True])
def test_gate_before_ai(client, monkeypatch, saved):
    if saved:
        client.post("/api/profile/raw", json={"raw_input": "Synthetic", "expected_version": 0})
    monkeypatch.setattr(
        client.app.state.models.judge_model, "invoke", lambda *a: pytest.fail("gate bypass")
    )
    response = analyze(client)
    assert response.status_code == 422
    run = client.get("/api/debug/runs/" + response.json()["run_id"]).json()
    assert len(run["nodes"]) == 1 and run["nodes"][0]["status"] == "failed"
    assert client.get("/api/jobs").json() == {"jobs": []}


@pytest.mark.parametrize(
    "field,value",
    [
        ("importance", "must"),
        ("category", "unknown"),
        ("scoreability", "YES"),
        ("requirement_id", "invented"),
    ],
)
def test_invalid_extraction(field, value):
    with pytest.raises(ValidationError):
        JobExtraction.model_validate(
            {"title": "Synthetic", "requirements": [{**requirement(), field: value}]}
        )


@pytest.mark.parametrize("word", ["身体健康", "吃苦耐劳", "爱岗敬业", "道德品质"])
def test_guard(word):
    guarded, adjusted = guard_requirements([requirement(text=word)])
    assert adjusted and guarded[0]["scoreability"] == "NON_SCORABLE"
    assert coverage(guarded, Matching(matches=[])) is None


def test_observable_collaboration_retained():
    original = [requirement(text="与产品、测试、算法团队协作交付")]
    assert guard_requirements(original) == (original, False)


@pytest.mark.parametrize(
    "matches",
    [
        [],
        [match(), match()],
        [match(index=1)],
        [match(ids=[])],
        [match("PARTIAL", [])],
        [match(ids=["unknown"])],
    ],
)
def test_invalid_matching(matches):
    with pytest.raises(CopilotError):
        validate_matches(
            [requirement()],
            Matching.model_validate({"matches": matches}),
            [{"id": "e1", "active": 1, "profile_version": 3}],
            3,
        )


@pytest.mark.parametrize("active,version", [(0, 3), (1, 2)])
def test_stale_evidence_rejected(active, version):
    with pytest.raises(CopilotError):
        validate_matches(
            [requirement()],
            Matching.model_validate({"matches": [match()]}),
            [{"id": "e1", "active": active, "profile_version": version}],
            3,
        )


@pytest.mark.parametrize("status,value", [("MATCH", 100), ("PARTIAL", 50), ("GAP", 0)])
def test_status_coverage(status, value):
    output = Matching.model_validate(
        {"matches": [match(status, [] if status == "GAP" else ["e1"])]}
    )
    validate_matches([requirement()], output, [{"id": "e1", "active": 1, "profile_version": 3}], 3)
    assert coverage([requirement()], output) == value


def test_weighting_rounding_and_exclusion():
    requirements = [
        requirement(),
        requirement(importance="preferred"),
        requirement(scoreability="NON_SCORABLE"),
        requirement(scoreability="NEEDS_CONFIRMATION"),
    ]
    assert (
        coverage(
            requirements, Matching.model_validate({"matches": [match(), match("PARTIAL", index=1)]})
        )
        == 83.3
    )
    assert coverage([], Matching(matches=[])) is None
    with pytest.raises(ValidationError):
        Matching.model_validate({"matches": [match("UNKNOWN")]})


@pytest.mark.parametrize(
    "table,condition",
    [("job_requirements", "NEW.sequence=1"), ("job_matches", "NEW.requirement_id='req-002'")],
)
def test_atomic_rollback(client, table, condition):
    confirmed(client)
    existing = analyze(client).json()["job"]
    before = client.get("/api/profile").json()
    with client.app.state.db.connect() as conn:
        conn.execute(
            f"CREATE TRIGGER fail_insert BEFORE INSERT ON {table} WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
    response = analyze(client)
    assert response.status_code == 500
    assert client.get("/api/jobs").json()["jobs"] == [existing]
    assert client.get("/api/profile").json() == before
    with client.app.state.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM job_requirements").fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM job_matches").fetchone()[0] == 2


@pytest.mark.parametrize("role", ["judge_model", "generation_model"])
def test_model_failure_preserves_business(client, monkeypatch, role):
    confirmed(client)
    old = analyze(client).json()["job"]
    before = client.get("/api/profile").json()

    def fail(*args):
        raise ValueError("Bearer secret sk-sensitive-data")

    monkeypatch.setattr(getattr(client.app.state.models, role), "invoke", fail)
    response = analyze(client)
    assert response.status_code == 500
    assert client.get("/api/jobs").json()["jobs"] == [old]
    assert client.get("/api/profile").json() == before
    run = client.get("/api/debug/runs/" + response.json()["run_id"]).json()
    assert run["nodes"][-1]["status"] == "failed"
    assert "sk-sensitive" not in json.dumps(run)


def test_concurrent_profile_edit_rejects_publication(client, monkeypatch):
    confirmed(client)
    model = client.app.state.models.generation_model
    original = model.invoke

    def mutate(*args):
        result = original(*args)
        client.post(
            "/api/profile/raw", json={"raw_input": "Changed during analysis", "expected_version": 3}
        )
        return result

    monkeypatch.setattr(model, "invoke", mutate)
    assert analyze(client).status_code == 409
    assert client.get("/api/jobs").json() == {"jobs": []}


def test_zero_scored_skips_matching(client, monkeypatch):
    confirmed(client)
    model = client.app.state.models.generation_model
    original = model.invoke
    extraction = JobRequirements(
        title="Synthetic",
        requirements=[{"text": "接受出差", "importance": "required", "category": "logistics"}],
    )

    def generate(task, *args):
        assert task != "matching-v1"
        return (
            ModelResult(output=extraction, metadata={})
            if task == "jd-requirements-v2"
            else original(task, *args)
        )

    monkeypatch.setattr(model, "invoke", generate)
    job = analyze(client).json()["job"]
    assert job["coverage"] is None and job["requirements"][0]["match"] is None


def test_job_replays_offline_no_writes(client, monkeypatch):
    confirmed(client)
    before = (
        client.get("/api/debug/runs").json(),
        client.get("/api/jobs").json(),
        client.get("/api/profile").json(),
    )
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("live"))
    root = Path(__file__).parents[2]
    for suite, task in [("jd_extraction", "jd-extract-v1"), ("matching", "matching-v1")]:
        case = json.loads((root / f"evals/{suite}/cases.json").read_text())[0]
        payload = case.get("payload") or {"raw_jd": case["raw_jd"]}
        response = client.post("/api/debug/replay", json={"task": task, "payload": payload})
        assert response.status_code == 200
        assert response.json()["output"] == case["expected"]
        assert response.json()["metadata"]["provider"] == "replay"
    assert before == (
        client.get("/api/debug/runs").json(),
        client.get("/api/jobs").json(),
        client.get("/api/profile").json(),
    )


def test_eval_metrics_detect_perturbations():
    expected = {"requirements": [requirement()]}
    actual = {
        "requirements": [
            requirement(importance="preferred", category="other", scoreability="NON_SCORABLE"),
            requirement(text="invented"),
        ]
    }
    metrics = extraction_metrics(expected, actual)
    assert (
        metrics["unsupported_addition"] == 1
        and metrics["importance_correct"] == 0
        and metrics["category_correct"] == 0
    )
    assert extraction_metrics(expected, {"requirements": []})["requirement_missing"] == 1
    assert (
        scoreability_metrics(expected["requirements"], actual["requirements"])["SCORED_correct"]
        == 0
    )
    metrics = matching_metrics(
        {"matches": [match("GAP", [])]}, {"matches": [match(ids=["unknown"])]}, []
    )
    assert (
        metrics["status_agreement"] == 0
        and metrics["valid_citations"] == 0
        and metrics["unsupported_positive"] == 1
    )


def test_v2_upgrade_keeps_profile(client):
    profile = confirmed(client)
    db = client.app.state.db
    with db.connect() as conn:
        conn.execute("PRAGMA user_version=2")
    db.initialize()
    assert client.get("/api/profile").json()["profile"] == profile
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


@pytest.mark.parametrize("jd", ["", "   ", "x" * 30001])
def test_invalid_jd(client, jd):
    assert client.post("/api/jobs/analyze", json={"raw_jd": jd}).status_code == 422


def test_matching_receives_all_active_evidence(client, monkeypatch):
    confirmed(client)
    model = client.app.state.models.generation_model
    original = model.invoke
    evidence = client.get("/api/profile").json()["profile"]["evidence"]

    def inspect(task, payload, schema):
        if task != "matching-v1":
            return original(task, payload, schema)
        assert {e["id"] for e in payload["evidence"]} == {e["id"] for e in evidence}
        assert all(r["scoreability"] == "SCORED" for r in payload["requirements"])
        assert all("raw_content" not in e for e in payload["evidence"])
        return original(task, payload, schema)

    monkeypatch.setattr(model, "invoke", inspect)
    assert analyze(client).status_code == 200


def test_guard_adjustment_is_traced(client, monkeypatch):
    confirmed(client)
    output = ScoreabilityResult(
        classifications=[{"requirement_index": i, "scoreability": "SCORED"} for i in range(4)]
    )
    monkeypatch.setattr(
        client.app.state.models.judge_model,
        "invoke",
        lambda *a: ModelResult(output=output, metadata={}),
    )
    result = analyze(client).json()
    assert result["job"]["requirements"][3]["scoreability"] == "NON_SCORABLE"
    run = client.get("/api/debug/runs/" + result["run_id"]).json()
    assert run["nodes"][4]["output_summary"]["scoreability_adjusted"] is True


def test_invalid_match_fails_at_validation_without_writes(client, monkeypatch):
    confirmed(client)
    original = client.app.state.models.generation_model.invoke
    invalid = Matching.model_validate({"matches": [match(ids=["inactive-or-invented"])]})
    monkeypatch.setattr(
        client.app.state.models.generation_model,
        "invoke",
        lambda *a: (
            ModelResult(output=invalid, metadata={}) if a[0] == "matching-v1" else original(*a)
        ),
    )
    response = analyze(client)
    assert (
        response.status_code == 500 and response.json()["error_code"] == "MATCH_VALIDATION_FAILED"
    )
    run = client.get("/api/debug/runs/" + response.json()["run_id"]).json()
    assert run["nodes"][-1]["node_name"] == "validate_matches"
    assert client.get("/api/jobs").json() == {"jobs": []}


def test_fake_matching_partial():
    from career_copilot.models.jobs import fake_matching

    output = fake_matching(
        {
            "requirements": [{**requirement(text="Python and C++"), "requirement_index": 0}],
            "evidence": [{"id": "e1", "normalized_content": "Python"}],
        }
    )
    assert output["matches"][0]["status"] == "PARTIAL"
    assert output["matches"][0]["evidence_ids"] == ["e1"]
