import json

from career_copilot.api import create_app
from career_copilot.database import Database
from career_copilot.errors import CopilotError, ErrorCode, WorkflowError
from career_copilot.models.router import ModelRouter
from career_copilot.tracing import RunTrace
from career_copilot.workflows.dummy import CareerState
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph


def test_http_graph_state_diff_trace_and_response(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Dummy graph must never invoke a model")

    monkeypatch.setattr(ModelRouter, "__init__", forbidden)
    response = client.post("/api/workflows/dummy", json={"message": "  private career text  "})
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == {
        "message": "private career text",
        "counter": 1,
        "result": "Ready: private career text",
    }
    run = client.get(f"/api/debug/runs/{body['run_id']}").json()
    assert run["status"] == "completed"
    assert run["workflow_version"] == "dummy-v1"
    assert run["completed_at"] and run["duration_ms"] >= 0
    assert [n["node_name"] for n in run["nodes"]] == ["prepare_input", "mutate_state"]
    assert [n["sequence"] for n in run["nodes"]] == [1, 2]
    node = run["nodes"][1]
    assert node["state_changes"]["counter"] == {"before": 0, "after": 1}
    assert set(node["state_changes"]) == {"counter", "result"}
    assert node["ai"] is None
    assert node["validation_result"]["status"] == "passed"
    assert all(n["duration_ms"] >= 0 and n["started_at"] for n in run["nodes"])
    assert "private career text" not in json.dumps(run)
    assert all("debug_payload" not in n for n in run["nodes"])
    listing = client.get("/api/debug/runs").json()["runs"]
    assert listing[0]["run_id"] == body["run_id"]
    assert listing[0]["total_tokens"] == 0
    assert listing[0]["models"] == []


def test_runs_survive_application_restart(settings):
    with TestClient(create_app(settings)) as client:
        run_id = client.post("/api/workflows/dummy", json={}).json()["run_id"]
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/debug/runs/{run_id}").json()["status"] == "completed"


def test_validation_and_missing_run(client):
    assert client.post("/api/workflows/dummy", json={"message": ""}).status_code == 422
    assert client.post("/api/workflows/dummy", json={"message": "a" * 501}).status_code == 422
    assert client.post("/api/workflows/dummy", json={"unknown": 1}).status_code == 422
    assert client.get("/api/debug/runs/no-such-id").status_code == 404
    assert client.get("/api/debug/runs?limit=101").status_code == 422
    assert client.get("/api/debug/runs?offset=-1").status_code == 422


def test_pagination_and_distinct_runs(client):
    ids = [client.post("/api/workflows/dummy", json={}).json()["run_id"] for _ in range(3)]
    assert len(set(ids)) == 3
    first = client.get("/api/debug/runs?limit=1").json()["runs"]
    second = client.get("/api/debug/runs?limit=1&offset=1").json()["runs"]
    assert first[0]["run_id"] != second[0]["run_id"]


def test_failed_node_is_locatable_and_exception_text_not_logged(settings):
    db = Database(settings.database_path)
    db.initialize()
    trace = RunTrace(db, settings, "failure-test", "v1")

    def fail(state):
        raise CopilotError(ErrorCode.MATCH_VALIDATION_FAILED)

    graph = StateGraph(CareerState)
    graph.add_node("validate", trace.node("validate", fail))
    graph.add_edge(START, "validate")
    graph.add_edge("validate", END)
    try:
        trace.execute(graph.compile(), {"message": "private", "counter": 0, "result": ""})
        raise AssertionError("Expected failure")
    except WorkflowError as exc:
        assert exc.run_id == trace.run_id
        assert exc.code == ErrorCode.MATCH_VALIDATION_FAILED
    run = db.get_run(trace.run_id)
    assert run["status"] == "failed"
    assert run["error_code"] == "MATCH_VALIDATION_FAILED"
    assert run["nodes"][0]["node_name"] == "validate"
    assert run["nodes"][0]["error_code"] == "MATCH_VALIDATION_FAILED"
    assert "private" not in json.dumps(run)


def test_unexpected_failure_uses_safe_error_code(settings):
    db = Database(settings.database_path)
    db.initialize()
    trace = RunTrace(db, settings, "failure-test", "v1")
    wrapped = trace.node("broken", lambda state: 1 / 0)
    db.start_run(
        {
            "run_id": trace.run_id,
            "workflow_name": "failure-test",
            "workflow_version": "v1",
            "started_at": "test",
            "status": "running",
        }
    )
    try:
        wrapped({})
    except CopilotError as exc:
        assert exc.code == ErrorCode.WORKFLOW_FAILED
    node = db.get_run(trace.run_id)["nodes"][0]
    assert node["status"] == "failed"
    assert "ZeroDivisionError" not in json.dumps(node)


def test_persistence_failure_is_classified(tmp_path):
    db = Database(tmp_path / "missing" / "db.sqlite3")
    try:
        db.list_runs()
        raise AssertionError("Expected persistence failure")
    except CopilotError as exc:
        assert exc.code == ErrorCode.PERSISTENCE_FAILED


def test_debug_api_unprefixed_alias(client):
    run_id = client.post("/api/workflows/dummy", json={}).json()["run_id"]
    assert client.get("/debug/runs").json()["runs"][0]["run_id"] == run_id
    assert client.get(f"/debug/runs/{run_id}").json()["run_id"] == run_id
