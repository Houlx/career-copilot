import copy
import json
from pathlib import Path

import pytest
from career_copilot.api import create_app
from career_copilot.errors import CopilotError
from career_copilot.models.resumes import (
    ResumeDraft,
    fake_resume,
    grounding_metrics,
    render_markdown,
    validate_grounding,
    validate_language,
)
from career_copilot.models.router import ModelResult
from fastapi.testclient import TestClient
from pydantic import ValidationError

EID = "career-work-" + "a" * 64
EVIDENCE = [
    {
        "id": EID,
        "active": 1,
        "profile_version": 3,
        "normalized_content": "Built Python 3.12 tools in 2024; improved latency 20%.",
    }
]


def draft():
    return fake_resume({"requested_language": "en", "evidence": EVIDENCE})


def setup_job(client):
    client.post(
        "/api/profile/raw", json={"raw_input": "Built Python tools in 2024.", "expected_version": 0}
    ).raise_for_status()
    client.post("/api/profile/normalize", json={"expected_version": 1}).raise_for_status()
    client.post("/api/profile/confirm", json={"expected_version": 2}).raise_for_status()
    return client.post("/api/jobs/analyze", json={"raw_jd": "Synthetic JD"}).json()["job"]["id"]


def generate(client, job, language="en"):
    return client.post(f"/api/jobs/{job}/resumes", json={"language": language})


def test_resume_flow_restart_history_delete_trace(client, settings):
    job = setup_job(client)
    assert client.get(f"/api/jobs/{job}/resumes").json() == {"resumes": []}
    results = []
    for language in ("zh", "en", "ja"):
        response = generate(client, job, language)
        assert response.status_code == 200, response.text
        body = response.json()
        resume = body["resume"]
        assert resume["language"] == language and not resume["outdated"]
        assert resume["evidence"] and resume["status"] == "completed"
        assert EID not in resume["markdown"]
        assert "career-" not in resume["markdown"]
        trace = client.get("/api/debug/runs/" + body["run_id"]).json()
        assert len(trace["nodes"]) == 7
        assert trace["nodes"][1]["ai"]["model_role"] == "generation"
        assert trace["nodes"][1]["ai"]["prompt_version"] == "resume-generation-v1"
        assert trace["nodes"][3]["validation_result"]["invalid_evidence_reference_count"] == 0
        assert all("debug_payload" not in n for n in trace["nodes"])
        assert "Built Python tools" not in json.dumps(trace)
        results.append(resume)
    assert len({r["id"] for r in results}) == 3
    assert len(client.get(f"/api/jobs/{job}/resumes").json()["resumes"]) == 3
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/resumes/" + results[0]["id"]).json()["resume"] == results[0]
    client.post("/api/profile/raw", json={"raw_input": "New synthetic data", "expected_version": 3})
    old = client.get("/api/resumes/" + results[0]["id"]).json()["resume"]
    assert old["outdated"] and old["evidence"] == results[0]["evidence"]
    client.delete("/api/jobs/" + job).raise_for_status()
    assert client.get("/api/resumes/" + old["id"]).json()["resume"]["outdated"]
    client.delete("/api/resumes/" + old["id"]).raise_for_status()
    assert client.get("/api/resumes/" + old["id"]).status_code == 404
    assert client.delete("/api/resumes/" + old["id"]).status_code == 404
    assert client.get("/api/profile").json()["profile"] is not None


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "JOB_NOT_FOUND"),
        ("incomplete", "JOB_NOT_COMPLETED"),
        ("outdated", "JOB_OUTDATED"),
        ("unconfirmed", "PROFILE_VALIDATION_FAILED"),
    ],
)
def test_gates_no_model(client, monkeypatch, change, code):
    job = setup_job(client)
    with client.app.state.db.connect() as conn:
        if change == "missing":
            conn.execute("DELETE FROM jobs WHERE id=?", (job,))
        elif change == "incomplete":
            conn.execute("UPDATE jobs SET status='draft'")
        elif change == "outdated":
            conn.execute("UPDATE jobs SET profile_version=2")
        else:
            conn.execute("UPDATE career_profile SET confirmed=0")
    monkeypatch.setattr(
        client.app.state.models.generation_model, "invoke", lambda *a: pytest.fail("gate bypassed")
    )
    response = generate(client, job)
    assert response.json()["error_code"] == code
    with client.app.state.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM resumes").fetchone()[0] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "invalid_id",
        "zero_refs",
        "duplicate_refs",
        "language",
        "empty_section",
        "duplicate_section",
        "empty_text",
    ],
)
def test_strict_schema(mutation):
    value = draft()
    if mutation == "missing":
        del value["summary"]
    elif mutation == "extra":
        value["unexpected"] = True
    elif mutation == "invalid_id":
        value["headline"]["evidence_ids"] = ["req-001"]
    elif mutation == "zero_refs":
        value["headline"]["evidence_ids"] = []
    elif mutation == "duplicate_refs":
        value["headline"]["evidence_ids"] = [EID, EID]
    elif mutation == "language":
        value["language"] = "de"
    elif mutation == "empty_section":
        value["sections"][0]["items"] = []
    elif mutation == "duplicate_section":
        value["sections"].append(value["sections"][0])
    elif mutation == "empty_text":
        value["headline"]["text"] = "   "
    with pytest.raises(ValidationError):
        ResumeDraft.model_validate(value)


@pytest.mark.parametrize(
    "mutation",
    ["nonexistent", "inactive", "version", "numeric", "headline_numeric", "summary_numeric"],
)
def test_grounding_rejects(mutation):
    evidence = copy.deepcopy(EVIDENCE)
    value = copy.deepcopy(draft())
    if mutation == "nonexistent":
        evidence = []
    elif mutation == "inactive":
        evidence[0]["active"] = 0
    elif mutation == "version":
        evidence[0]["profile_version"] = 2
    elif mutation == "numeric":
        value["sections"][0]["items"][0]["text"] = "Improved 99%"
    elif mutation == "headline_numeric":
        value["headline"]["text"] = "10 years experience"
    else:
        value["summary"][0]["text"] = "Managed 50 people"
    with pytest.raises(CopilotError):
        validate_grounding(ResumeDraft.model_validate(value), evidence, 3)


def test_multiple_refs_numeric_and_language_renderer():
    value = copy.deepcopy(draft())
    second = {
        **EVIDENCE[0],
        "id": "career-skill-" + "b" * 64,
        "normalized_content": "Managed 4 applications",
    }
    value["headline"] = {"text": "Python 3.12; 4 applications", "evidence_ids": [EID, second["id"]]}
    parsed = ResumeDraft.model_validate(value)
    metrics = validate_grounding(parsed, [*EVIDENCE, second], 3)
    assert metrics["evidence_backed_item_rate"] == 1
    with pytest.raises(CopilotError):
        validate_language(parsed, "ja")
    expected = (
        "# Python 3\\.12; 4 applications\n\n## Summary\n\n"
        "Built Python 3\\.12 tools in 2024; improved latency 20%\\.\n\n"
        "## Skills\n\n- Built Python 3\\.12 tools in 2024; improved latency 20%\\.\n\n"
        "## Experience\n\n- Built Python 3\\.12 tools in 2024; improved latency 20%\\.\n"
    )
    assert render_markdown(parsed) == expected
    assert render_markdown(parsed) == render_markdown(parsed)


@pytest.mark.parametrize(
    "change", ["profile", "job_deleted", "job_incomplete", "evidence_inactive"]
)
def test_final_transaction_race_rejected(client, monkeypatch, change):
    job = setup_job(client)
    original = client.app.state.models.generation_model.invoke

    def invoke(*args, **kwargs):
        result = original(*args, **kwargs)
        with client.app.state.db.connect() as conn:
            if change == "profile":
                conn.execute(
                    "UPDATE career_profile SET profile_version=profile_version+1,confirmed=0"
                )
            elif change == "job_deleted":
                conn.execute("DELETE FROM jobs")
            elif change == "job_incomplete":
                conn.execute("UPDATE jobs SET status='failed'")
            else:
                conn.execute("UPDATE career_evidence SET active=0")
        return result

    monkeypatch.setattr(client.app.state.models.generation_model, "invoke", invoke)
    response = generate(client, job)
    assert response.status_code != 200
    if change == "profile":
        assert response.json()["error_code"] == "PROFILE_CONFLICT"
    with client.app.state.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM resumes").fetchone()[0] == 0


def test_bad_model_grounding_atomic(client, monkeypatch):
    job = setup_job(client)
    original = client.app.state.models.generation_model.invoke

    def invoke(*args, **kwargs):
        result = original(*args, **kwargs)
        value = result.output.model_dump()
        value["headline"]["text"] = "Managed 987 people"
        return ModelResult(output=ResumeDraft.model_validate(value), metadata=result.metadata)

    monkeypatch.setattr(client.app.state.models.generation_model, "invoke", invoke)
    response = generate(client, job)
    assert response.json()["error_code"] == "RESUME_GROUNDING_FAILED"
    assert client.get(f"/api/jobs/{job}/resumes").json() == {"resumes": []}


def test_language_request_required(client):
    job = setup_job(client)
    for body in ({}, {"language": "de"}, {"language": "en", "extra": 1}):
        assert client.post(f"/api/jobs/{job}/resumes", json=body).status_code == 422


def test_resume_replay_isolated(client):
    case = json.loads(Path("evals/resumes/cases.json").read_text())[0]
    before = client.get("/api/debug/runs").json()
    response = client.post(
        "/api/debug/replay", json={"task": "resume-generation-v1", "payload": case["payload"]}
    )
    response.raise_for_status()
    assert response.json()["output"] == case["expected"]
    assert response.json()["metadata"]["provider"] == "replay"
    assert client.get("/api/debug/runs").json() == before
    assert client.get("/api/profile").json() == {"profile": None}
    missing = client.post("/api/debug/replay", json={"task": "resume-generation-v1", "payload": {}})
    assert missing.json()["error_code"] == "REPLAY_NOT_FOUND"


def test_schema_v3_migration_preserves_artifacts(tmp_path):
    from career_copilot.database import Database

    db = Database(tmp_path / "migration.sqlite3")
    db.initialize()
    with db.connect() as conn:
        conn.execute("DROP TABLE resumes")
        conn.execute("PRAGMA user_version=3")
        conn.execute(
            "INSERT INTO jobs VALUES ('old','JD','role','completed',3,NULL,'date','date','date')"
        )
    db.initialize()
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT id FROM jobs").fetchone()[0] == "old"
        assert conn.execute("SELECT count(*) FROM resumes").fetchone()[0] == 0


def test_metrics_detect_unsupported_constructed_item():
    parsed = ResumeDraft.model_validate(draft())
    parsed.headline.evidence_ids = []
    assert grounding_metrics(parsed, EVIDENCE, 3)["unsupported_fact_item_count"] == 1
