import json

import pytest
from career_copilot.api import create_app
from career_copilot.database import Database
from career_copilot.errors import CopilotError, ErrorCode
from career_copilot.models.prompts import PROMPTS
from career_copilot.models.revisions import ResumeRevisionProposal, fake_revision
from career_copilot.models.router import ModelResult
from career_copilot.revision_store import RevisionStore
from career_copilot.workflows.revisions import MAX_AGENT_STEPS, agent_decide
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_resumes import generate, setup_job


def setup(client):
    job = setup_job(client)
    resume = generate(client, job).json()["resume"]
    expected = {
        "expected_resume_id": resume["id"],
        "expected_profile_version": resume["profile_version"],
    }
    response = client.post(f"/api/resumes/{resume['id']}/revision-threads", json=expected)
    response.raise_for_status()
    return resume, expected, response.json()["thread"]


def send(client, thread, expected, content="突出性能优化"):
    return client.post(
        f"/api/revision-threads/{thread['id']}/messages", json={**expected, "content": content}
    )


def resolve(client, proposal_id, expected, action="accept"):
    return client.post(f"/api/revision-proposals/{proposal_id}/{action}", json=expected)


def test_proposal_reject_accept_history_restart_trace(client, settings):
    parent, expected, thread = setup(client)
    result = send(client, thread, expected)
    result.raise_for_status()
    proposal_id = result.json()["proposal_id"]
    assert client.get("/api/resumes/" + parent["id"]).json()["resume"] == parent
    assert len(client.get(f"/api/jobs/{parent['job_id']}/resumes").json()["resumes"]) == 1
    trace = client.get("/api/debug/runs/" + result.json()["run_id"]).json()
    decision = trace["nodes"][1]
    assert decision["agent_action"] == "REVISE" and decision["agent_step"] == 1
    assert decision["ai"]["reasoning_effort"] == "high"
    assert decision["ai"]["reasoning_source"] == "task"
    assert decision["ai"]["schema_contract_hash"]
    assert "突出性能优化" not in json.dumps(trace, ensure_ascii=False)
    rejected = resolve(client, proposal_id, expected, "reject")
    rejected.raise_for_status()
    assert rejected.json()["thread"]["proposals"][0]["status"] == "REJECTED"
    assert client.get("/api/resumes/" + parent["id"]).json()["resume"] == parent
    assert resolve(client, proposal_id, expected).status_code == 409
    proposal_id = send(client, thread, expected, "调整顺序").json()["proposal_id"]
    accepted = resolve(client, proposal_id, expected)
    accepted.raise_for_status()
    child = accepted.json()["resume"]
    assert child["id"] != parent["id"] and child["parent_resume_id"] == parent["id"]
    assert child["revision_number"] == 1
    assert client.get("/api/resumes/" + parent["id"]).json()["resume"] == parent
    assert resolve(client, proposal_id, expected).status_code == 409
    assert send(client, thread, expected).status_code == 409
    new_expected = {**expected, "expected_resume_id": child["id"]}
    next_proposal = send(client, thread, new_expected).json()["proposal_id"]
    grandchild = resolve(client, next_proposal, new_expected).json()["resume"]
    assert grandchild["parent_resume_id"] == child["id"] and grandchild["revision_number"] == 2
    with TestClient(create_app(settings)) as restarted:
        history = restarted.get(f"/api/resumes/{grandchild['id']}/revision-thread").json()["thread"]
        assert len(history["history"]) == 2
        assert history["current_resume_id"] == grandchild["id"]
    assert client.delete("/api/resumes/" + parent["id"]).status_code == 409


@pytest.mark.parametrize(
    "instruction,action",
    [("更厉害一点", "ASK_USER"), ("加 CUDA", "NO_CHANGE"), ("改成英文", "NO_CHANGE")],
)
def test_responses_no_artifact(client, instruction, action):
    parent, expected, thread = setup(client)
    response = send(client, thread, expected, instruction)
    response.raise_for_status()
    assert response.json()["proposal_id"] is None
    assert not response.json()["thread"]["proposals"]
    trace = client.get("/api/debug/runs/" + response.json()["run_id"]).json()
    assert trace["nodes"][1]["agent_action"] == action and len(trace["nodes"]) == 3
    assert client.get("/api/resumes/" + parent["id"]).json()["resume"] == parent


@pytest.mark.parametrize("mutation", ["invalid_ref", "numeric", "language"])
def test_validation_rejects_no_pending(client, monkeypatch, mutation):
    parent, expected, thread = setup(client)
    original = client.app.state.models.generation_model.invoke

    def invoke(*args, **kwargs):
        result = original(*args, **kwargs)
        value = result.output.model_dump()
        if mutation == "invalid_ref":
            value["updated_resume"]["summary"][0]["evidence_ids"] = ["career-work-" + "f" * 64]
        elif mutation == "numeric":
            value["updated_resume"]["summary"][0]["text"] = "Improved 987%"
        else:
            value["updated_resume"]["language"] = "ja"
        return ModelResult(
            output=ResumeRevisionProposal.model_validate(value), metadata=result.metadata
        )

    monkeypatch.setattr(client.app.state.models.generation_model, "invoke", invoke)
    response = send(client, thread, expected)
    assert response.status_code == 422
    assert response.json()["error_code"] == "REVISION_VALIDATION_FAILED"
    assert client.get("/api/resumes/" + parent["id"]).json()["resume"] == parent
    assert not client.get(f"/api/resumes/{parent['id']}/revision-thread").json()["thread"][
        "proposals"
    ]


@pytest.mark.parametrize(
    "mutation", ["profile", "unconfirmed", "job", "deleted_job", "inactive", "numeric"]
)
def test_accept_rechecks_transaction(client, mutation):
    parent, expected, thread = setup(client)
    proposal_id = send(client, thread, expected).json()["proposal_id"]
    with client.app.state.db.connect() as conn:
        if mutation == "profile":
            conn.execute("UPDATE career_profile SET profile_version=profile_version+1")
        elif mutation == "unconfirmed":
            conn.execute("UPDATE career_profile SET confirmed=0")
        elif mutation == "job":
            conn.execute("UPDATE jobs SET status='draft'")
        elif mutation == "deleted_job":
            conn.execute("DELETE FROM jobs")
        elif mutation == "inactive":
            conn.execute("UPDATE career_evidence SET active=0")
        else:
            row = conn.execute(
                "SELECT proposal_json FROM resume_revision_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            value = json.loads(row[0])
            value["updated_resume"]["summary"][0]["text"] = "987%"
            conn.execute(
                "UPDATE resume_revision_proposals SET proposal_json=? WHERE id=?",
                (json.dumps(value), proposal_id),
            )
    response = resolve(client, proposal_id, expected)
    assert response.status_code == 409
    with client.app.state.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM resumes").fetchone()[0] == 1
        assert (
            conn.execute("SELECT status FROM resume_revision_proposals").fetchone()[0] == "PENDING"
        )
        assert conn.execute("SELECT count(*) FROM resume_revision_lineage").fetchone()[0] == 0


def test_supersede_isolation_and_racing_generation(client):
    parent, expected, thread = setup(client)
    first = send(client, thread, expected).json()["proposal_id"]
    second = send(client, thread, expected, "调整顺序").json()
    assert [p["status"] for p in second["thread"]["proposals"]] == ["SUPERSEDED", "PENDING"]
    assert resolve(client, first, expected).status_code == 409
    other = generate(client, parent["job_id"]).json()["resume"]
    assert client.get(f"/api/resumes/{other['id']}/revision-thread").json()["thread"] is None
    store = RevisionStore(client.app.state.db)
    instruction = {**expected, "content": "突出性能优化"}
    earlier = store.begin(thread["id"], instruction)
    latest = store.begin(thread["id"], instruction)
    earlier["proposal"] = fake_revision(earlier["context"])
    with pytest.raises(CopilotError, match="REVISION_PROPOSAL_CONFLICT"):
        store.finish(earlier)
    latest["proposal"] = fake_revision(latest["context"])
    assert store.finish(latest)
    assert store.get(thread["id"])["current_resume_id"] == parent["id"]
    assert len(latest["context"]["conversation_messages"]) <= 10


def test_step_limit_no_invocation():
    with pytest.raises(CopilotError) as exc:
        agent_decide({"agent_step_count": MAX_AGENT_STEPS}, None)
    assert exc.value.code == ErrorCode.REVISION_STEP_LIMIT_EXCEEDED


def test_strict_contract_and_action_content():
    value = {
        "action": "ASK_USER",
        "assistant_message": "Which emphasis?",
        "updated_resume": None,
        "change_summary": [],
    }
    assert PROMPTS["resume-revision-agent-v1"].response_model is ResumeRevisionProposal
    for mutation in (
        {"extra": 1},
        {"action": "CALL_TOOL"},
        {"action": "REVISE"},
        {"change_summary": [{"section_kind": "x", "summary": "y"}]},
    ):
        with pytest.raises(ValidationError):
            ResumeRevisionProposal.model_validate({**value, **mutation})


def test_atomic_insert_failure_rolls_back(client):
    parent, expected, thread = setup(client)
    proposal_id = send(client, thread, expected).json()["proposal_id"]
    with client.app.state.db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_lineage BEFORE INSERT ON "
            "resume_revision_lineage BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
    assert resolve(client, proposal_id, expected).status_code == 500
    with client.app.state.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM resumes").fetchone()[0] == 1
        assert (
            conn.execute("SELECT status FROM resume_revision_proposals").fetchone()[0] == "PENDING"
        )


def test_v4_migration_and_future_version(tmp_path):
    db = Database(tmp_path / "migration.db")
    db.initialize()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO resumes VALUES "
            "('old','missing-job',3,'en','completed','{}','saved','[]','date')"
        )
        conn.execute(
            "INSERT INTO workflow_runs VALUES "
            "('old-run','old','v1','date',NULL,NULL,'running',NULL)"
        )
        for table in (
            "resume_revision_lineage",
            "resume_revision_proposals",
            "resume_revision_messages",
            "resume_revision_threads",
        ):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("PRAGMA user_version=4")
    db.initialize()
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("SELECT markdown FROM resumes").fetchone()[0] == "saved"
        assert conn.execute("SELECT run_id FROM workflow_runs").fetchone()[0] == "old-run"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.execute("PRAGMA user_version=7")
    with pytest.raises(CopilotError):
        db.initialize()


def test_revision_replay_isolated_and_metrics_perturbation(client):
    from pathlib import Path

    from career_copilot.models.resumes import ResumeDraft, grounding_metrics

    cases = json.loads(Path("evals/resume_revision/cases.json").read_text())
    before = client.get("/api/debug/runs").json()
    for case in cases:
        response = client.post(
            "/api/debug/replay",
            json={"task": "resume-revision-agent-v1", "payload": case["payload"]},
        )
        response.raise_for_status()
        assert response.json()["output"]["action"] == case["expected_action"]
        assert response.json()["metadata"]["provider"] == "replay"
    assert client.get("/api/debug/runs").json() == before
    assert client.get("/api/profile").json() == {"profile": None}
    value = cases[0]["payload"]["current_resume"]
    parsed = ResumeDraft.model_validate(value)
    parsed.summary[0].evidence_ids = ["career-work-" + "f" * 64]
    evidence = [{**e, "active": 1, "profile_version": 3} for e in cases[0]["payload"]["evidence"]]
    metrics = grounding_metrics(parsed, evidence, 3)
    assert metrics["invalid_evidence_reference_count"] == 1
    assert metrics["unsupported_fact_item_count"] == 1
