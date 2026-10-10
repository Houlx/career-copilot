import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from career_copilot.api import create_app
from career_copilot.database import Database
from career_copilot.errors import CopilotError, ErrorCode
from career_copilot.models.profile import NormalizedProfile, ProfileFacts
from career_copilot.models.router import ModelResult, ModelRouter, replay_key
from career_copilot.profile_eval import evaluate
from career_copilot.profile_store import ProfileStore
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

ROOT = Path(__file__).parents[2]
CASES = json.loads((ROOT / "evals/profile_extraction/cases.json").read_text())


def save(client, raw="使用 Python。", version=0):
    response = client.post("/api/profile/raw", json={"raw_input": raw, "expected_version": version})
    assert response.status_code == 200, response.text
    return response.json()["profile"]


def normalize(client, profile):
    response = client.post(
        "/api/profile/normalize", json={"expected_version": profile["profile_version"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, profile):
    response = client.post(
        "/api/profile/confirm", json={"expected_version": profile["profile_version"]}
    )
    assert response.status_code == 200, response.text
    return response.json()["profile"]


def test_complete_flow_restart_and_noop_stability(client, settings, monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("Live client constructed"))
    assert client.get("/api/profile").json() == {"profile": None}
    profile = save(client, "  使用 Python。\n")
    assert profile["state"] == "raw_saved"
    assert profile["profile_version"] == 1
    assert client.post("/api/profile/confirm", json={"expected_version": 1}).status_code == 422
    result = normalize(client, profile)
    profile = result["profile"]
    assert not profile["confirmed"]
    assert profile["profile_version"] == 2
    assert profile["raw_input"] == "  使用 Python。\n"
    profile = confirm(client, profile)
    assert profile["confirmed"] and profile["profile_version"] == 3
    evidence = profile["evidence"]
    assert len(evidence) == 1 and evidence[0]["id"].startswith("career-other-")
    assert json.loads(evidence[0]["raw_content"])["source_quote"] == profile["raw_input"]
    assert confirm(client, profile) == profile
    assert save(client, profile["raw_input"], 3) == profile
    assert normalize(client, profile)["profile"] == profile
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/profile").json()["profile"] == profile
    assert ProfileStore(client.app.state.db).confirmed_profile()["confirmed"]


def test_edit_preserves_raw_ai_and_requires_confirmation(client, monkeypatch):
    profile = normalize(client, save(client))["profile"]
    profile = confirm(client, profile)
    ai = profile["ai_normalized_profile"]
    edited = json.loads(json.dumps(profile["effective_profile"]))
    edited["entries"][0]["content"] = "使用 Python 编写脚本（用户补充）。"
    monkeypatch.setattr(
        client.app.state.models.generation_model, "invoke", lambda *a: pytest.fail("AI")
    )
    response = client.put("/api/profile", json={"expected_version": 3, "profile": edited})
    draft = response.json()["profile"]
    assert not draft["confirmed"] and draft["profile_version"] == 4
    assert draft["raw_input"] == profile["raw_input"]
    assert draft["ai_normalized_profile"] == ai
    assert draft["user_edited_profile"] == edited
    with pytest.raises(CopilotError):
        ProfileStore(client.app.state.db).confirmed_profile()
    same = client.put("/api/profile", json={"expected_version": 4, "profile": edited})
    assert same.json()["profile"] == draft
    updated = confirm(client, draft)
    assert updated["profile_version"] == 5
    assert updated["evidence"][0]["id"] != profile["evidence"][0]["id"]
    provenance = json.loads(updated["evidence"][0]["raw_content"])
    assert provenance["user_edit"]["content"] == edited["entries"][0]["content"]
    assert confirm(client, updated) == updated


@pytest.mark.parametrize(
    "task,code",
    [
        ("profile-facts-v1", ErrorCode.PROFILE_EXTRACTION_FAILED),
        ("profile-normalize-v1", ErrorCode.PROFILE_NORMALIZATION_FAILED),
        ("profile-facts-v1", ErrorCode.MODEL_REQUEST_FAILED),
        ("profile-normalize-v1", ErrorCode.MODEL_OUTPUT_INVALID),
    ],
)
def test_failed_ai_preserves_confirmed_profile(client, monkeypatch, task, code):
    profile = confirm(client, normalize(client, save(client))["profile"])

    original = client.app.state.models.generation_model.invoke

    def fail(*args):
        if args[0] != task:
            return original(*args)
        if code in {ErrorCode.PROFILE_EXTRACTION_FAILED, ErrorCode.PROFILE_NORMALIZATION_FAILED}:
            raise RuntimeError("private failure text")
        raise CopilotError(code)

    monkeypatch.setattr(client.app.state.models.generation_model, "invoke", fail)
    response = client.post("/api/profile/normalize", json={"expected_version": 3})
    assert response.status_code == 500
    assert response.json()["error_code"] == code.value
    assert client.get("/api/profile").json()["profile"] == profile
    run = client.get("/api/debug/runs/" + response.json()["run_id"]).json()
    assert run["status"] == "failed" and run["nodes"][-1]["status"] == "failed"
    assert "private failure text" not in json.dumps(run)


def test_new_raw_preserves_history_snapshot_and_blocks_stale_confirm(client, monkeypatch):
    old = confirm(client, normalize(client, save(client))["profile"])
    new = save(client, "使用 TypeScript。", 3)
    assert not new["confirmed"] and new["state"] == "raw_saved"
    assert new["last_confirmed_profile"]["profile"] == old["effective_profile"]
    assert [v["raw_input"] for v in new["raw_history"]] == [old["raw_input"], new["raw_input"]]
    assert new["evidence"] == old["evidence"]
    assert client.post("/api/profile/confirm", json={"expected_version": 4}).status_code == 422

    def fail(*a):
        raise CopilotError(ErrorCode.MODEL_OUTPUT_INVALID)

    monkeypatch.setattr(client.app.state.models.generation_model, "invoke", fail)
    assert client.post("/api/profile/normalize", json={"expected_version": 4}).status_code == 500
    assert client.get("/api/profile").json()["profile"] == new


@pytest.mark.parametrize("operation", ["insert", "profile-update"])
def test_confirmation_transaction_rollback(client, operation):
    draft = normalize(client, save(client))["profile"]
    db = client.app.state.db
    with db.connect() as conn:
        trigger = (
            "BEFORE INSERT ON career_evidence"
            if operation == "insert"
            else "BEFORE UPDATE ON career_profile WHEN NEW.confirmed=1"
        )
        conn.execute(
            f"CREATE TRIGGER fail_confirm {trigger} BEGIN SELECT RAISE(ABORT, 'fail'); END"
        )
    response = client.post("/api/profile/confirm", json={"expected_version": 2})
    assert response.status_code == 500
    assert response.json()["error_code"] == "PERSISTENCE_FAILED"
    assert client.get("/api/profile").json()["profile"] == draft
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM career_evidence").fetchone()[0] == 0


def test_reconfirmation_failure_rolls_back_evidence_deactivation(client):
    old = confirm(client, normalize(client, save(client))["profile"])
    changed = json.loads(json.dumps(old["effective_profile"]))
    changed["entries"][0]["title"] = "用户标题"
    draft = client.put("/api/profile", json={"expected_version": 3, "profile": changed}).json()[
        "profile"
    ]
    with client.app.state.db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_insert BEFORE INSERT ON career_evidence "
            "BEGIN SELECT RAISE(ABORT, 'fail'); END"
        )
    assert client.post("/api/profile/confirm", json={"expected_version": 4}).status_code == 500
    assert client.get("/api/profile").json()["profile"] == draft
    assert draft["evidence"] == old["evidence"]


def test_v1_upgrade_preserves_trace(settings):
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.database_path) as conn:
        conn.executescript("""
            CREATE TABLE workflow_runs (run_id TEXT PRIMARY KEY, workflow_name TEXT NOT NULL,
            workflow_version TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
            duration_ms REAL,status TEXT NOT NULL,error_code TEXT);
            CREATE TABLE node_traces (run_id TEXT,sequence INTEGER,node_name TEXT,status TEXT,
            trace_json TEXT,PRIMARY KEY(run_id,sequence));
            INSERT INTO workflow_runs VALUES
                ('old','dummy','dummy-v1','date',NULL,1,'completed',NULL);
            INSERT INTO node_traces VALUES ('old',1,'prepare_input','completed','{"sequence":1}');
            PRAGMA user_version=1;
        """)
    db = Database(settings.database_path)
    db.initialize()
    db.initialize()
    assert db.get_run("old")["nodes"] == [{"sequence": 1}]
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_trace_metadata_and_hidden_payload(client):
    result = normalize(client, save(client, "private career text"))
    run = client.get("/api/debug/runs/" + result["run_id"]).json()
    assert run["workflow_name"] == "profile_workflow"
    assert [n["node_name"] for n in run["nodes"]] == [
        "load_raw_profile",
        "extract_profile_facts",
        "validate_profile_facts",
        "normalize_profile",
        "validate_normalized_profile",
        "persist_profile_draft",
    ]
    for index, role, version in [
        (1, "generation", "profile-facts-v1"),
        (3, "generation", "profile-normalize-v1"),
    ]:
        node = run["nodes"][index]
        assert node["ai"]["model_role"] == role
        assert node["ai"]["prompt_version"] == version
        assert node["ai"]["total_tokens"] == 0
        assert node["ai"]["latency_ms"] >= 0
        assert node["ai"]["retry_count"] == 0
        assert node["state_changes"]
    assert "private career text" not in json.dumps(run)
    assert all("debug_payload" not in n for n in run["nodes"])


def test_profile_payload_redaction(settings):
    settings.ai_debug_payloads = True
    settings.api_key = SecretStr("sensitive-example")
    with TestClient(create_app(settings)) as client:
        result = normalize(client, save(client, "sensitive-example Bearer xyz sk-secret123"))
        run = client.get("/api/debug/runs/" + result["run_id"]).json()
        serialized = json.dumps(run)
        assert "sensitive-example" not in serialized and "sk-secret123" not in serialized
        assert "Bearer xyz" not in serialized
        assert run["nodes"][1]["debug_payload"]["model"]["output"]
        settings.ai_debug_payloads = False
        assert all(
            "debug_payload" not in n
            for n in client.get("/api/debug/runs/" + result["run_id"]).json()["nodes"]
        )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_reviewed_replay_pipeline_and_metrics(settings, case):
    settings.ai_mode = "replay"
    with TestClient(create_app(settings)) as client:
        result = normalize(client, save(client, case["raw_input"]))
        assert result["profile"]["extracted_facts"] == case["expected"]
        confirmed = confirm(client, result["profile"])
        assert confirmed["evidence"][0]["type"] == case["expected"]["facts"][0]["type"]
        score = evaluate(
            ProfileFacts.model_validate(case["expected"]),
            ProfileFacts.model_validate(result["profile"]["extracted_facts"]),
        )
        assert score["fact_omission"] == score["unsupported_fact_addition"] == 0
        assert score["type_correct"] == score["type_total"]
        assert score["numeric_preserved"] == score["numeric_total"]


@pytest.mark.parametrize(
    "task,schema", [("profile-facts-v1", ProfileFacts), ("profile-normalize-v1", NormalizedProfile)]
)
def test_standalone_profile_replay_offline_in_live_mode(settings, monkeypatch, task, schema):
    settings.ai_mode, settings.model, settings.api_key = "live", "test", SecretStr("test")
    case = CASES[3]
    payload = {"raw_input": case["raw_input"]} if schema is ProfileFacts else case["expected"]
    with TestClient(create_app(settings)) as client:

        def forbidden(**kwargs):
            pytest.fail("Live HTTP constructed")

        monkeypatch.setattr(httpx, "Client", forbidden)
        for _ in range(2):
            response = client.post("/api/debug/replay", json={"task": task, "payload": payload})
            assert response.status_code == 200
            schema.model_validate(response.json()["output"])
            assert response.json()["metadata"]["provider"] == "replay"
        assert client.get("/api/profile").json() == {"profile": None}
        assert client.get("/api/debug/runs").json() == {"runs": []}
        with client.app.state.db.connect() as conn:
            assert conn.execute("SELECT count(*) FROM career_evidence").fetchone()[0] == 0


@pytest.mark.parametrize(
    "schema,task", [(ProfileFacts, "profile-facts-v1"), (NormalizedProfile, "profile-normalize-v1")]
)
def test_invalid_replay_output(settings, tmp_path, schema, task):
    settings.ai_mode, settings.replay_dir = "replay", tmp_path
    (tmp_path / replay_key(task, task, {})).write_text(
        json.dumps({"task": task, "prompt_version": task, "output": {"unexpected": "fact"}})
    )
    role = "generation_model"
    with pytest.raises(CopilotError) as error:
        getattr(ModelRouter(settings), role).invoke(task, {}, schema)
    assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID


def test_schema_disallows_extra_fields_and_wrong_types():
    facts = json.loads(json.dumps(CASES[0]["expected"]))
    facts["facts"][0]["inferred_years"] = 5
    with pytest.raises(ValidationError):
        ProfileFacts.model_validate(facts)
    with pytest.raises(ValidationError):
        NormalizedProfile.model_validate({"entries": [{"source_index": "0"}]})


@pytest.mark.parametrize("bad_output", ["quote", "index", "number", "type", "omission"])
def test_grounding_validation_preserves_old_profile(client, monkeypatch, bad_output):
    old = confirm(client, normalize(client, save(client))["profile"])
    original = client.app.state.models.generation_model.invoke
    if bad_output == "quote":
        facts = ProfileFacts.model_validate(CASES[0]["expected"])
        monkeypatch.setattr(
            client.app.state.models.generation_model,
            "invoke",
            lambda *a: ModelResult(output=facts, metadata={}),
        )
    else:
        data = json.loads(json.dumps(old["effective_profile"]))
        if bad_output == "index":
            data["entries"][0]["source_index"] = 5
        elif bad_output == "number":
            data["entries"][0]["content"] += " 100%"
        elif bad_output == "type":
            data["entries"][0]["type"] = "education"
        else:
            data["entries"].append(dict(data["entries"][0]))
        output = NormalizedProfile.model_validate(data)
        monkeypatch.setattr(
            client.app.state.models.generation_model,
            "invoke",
            lambda *a: (
                ModelResult(output=output, metadata={})
                if a[0] == "profile-normalize-v1"
                else original(*a)
            ),
        )
    response = client.post("/api/profile/normalize", json={"expected_version": 3})
    assert response.json()["error_code"] == "PROFILE_VALIDATION_FAILED"
    assert client.get("/api/profile").json()["profile"] == old


def test_optimistic_concurrency_prevents_lost_updates(client, monkeypatch):
    profile = save(client)
    assert (
        client.post(
            "/api/profile/raw", json={"raw_input": "stale", "expected_version": 0}
        ).status_code
        == 409
    )
    original = client.app.state.models.generation_model.invoke

    def concurrent_edit(*args):
        if args[0] == "profile-normalize-v1":
            save(client, "new input", 1)
        return original(*args)

    monkeypatch.setattr(client.app.state.models.generation_model, "invoke", concurrent_edit)
    response = client.post(
        "/api/profile/normalize", json={"expected_version": profile["profile_version"]}
    )
    assert response.status_code == 409
    current = client.get("/api/profile").json()["profile"]
    assert current["raw_input"] == "new input" and current["ai_normalized_profile"] is None


def test_eval_detects_errors():
    expected = ProfileFacts.model_validate(CASES[4]["expected"])
    changed = expected.model_dump()
    changed["facts"][0]["type"] = "education"
    changed["facts"][0]["content"] = "延迟 999ms"
    changed["facts"].append(CASES[0]["expected"]["facts"][0])
    score = evaluate(expected, ProfileFacts.model_validate(changed))
    assert score["fact_omission"] == 1 and score["unsupported_fact_addition"] == 2
    assert score["type_correct"] == 0 and score["numeric_preserved"] == 0


def test_draft_persistence_failure_preserves_previous_confirmed(client):
    old = confirm(client, normalize(client, save(client))["profile"])
    # Trigger a genuine replacement instead of the unchanged normalization no-op.
    changed = json.loads(json.dumps(old["effective_profile"]))
    changed["entries"][0]["title"] = "Edited"
    draft = client.put("/api/profile", json={"expected_version": 3, "profile": changed}).json()[
        "profile"
    ]
    with client.app.state.db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_draft BEFORE UPDATE ON career_profile "
            "WHEN NEW.ai_normalized_profile IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT, 'fail'); END"
        )
    response = client.post("/api/profile/normalize", json={"expected_version": 4})
    assert response.json()["error_code"] == "PERSISTENCE_FAILED"
    assert client.get("/api/profile").json()["profile"] == draft
    assert draft["last_confirmed_profile"] == old["last_confirmed_profile"]


def test_confirm_multiple_evidence_rolls_back_partial_insert(client, monkeypatch):
    facts = {"facts": [CASES[0]["expected"]["facts"][0], CASES[1]["expected"]["facts"][0]]}
    raw = "\n".join(f["source_quote"] for f in facts["facts"])
    original = client.app.state.models.generation_model.invoke
    monkeypatch.setattr(
        client.app.state.models.generation_model,
        "invoke",
        lambda *a: (
            ModelResult(output=ProfileFacts.model_validate(facts), metadata={})
            if a[0] == "profile-facts-v1"
            else original(*a)
        ),
    )
    draft = normalize(client, save(client, raw))["profile"]
    with client.app.state.db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON career_evidence "
            "WHEN NEW.type='work' BEGIN SELECT RAISE(ABORT, 'fail'); END"
        )
    response = client.post("/api/profile/confirm", json={"expected_version": 2})
    assert response.status_code == 500
    assert client.get("/api/profile").json()["profile"] == draft
    with client.app.state.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM career_evidence").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_second")
    confirmed = confirm(client, draft)
    assert len(confirmed["evidence"]) == 2
    assert confirm(client, confirmed)["evidence"] == confirmed["evidence"]


def test_mock_profile_provider_roles_grounding_and_usage(settings, monkeypatch):
    real_client = httpx.Client
    requests = []
    facts = CASES[3]["expected"]

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "test-generation"
        if body["response_format"]["json_schema"]["name"] == "ProfileFacts":
            assert json.loads(body["messages"][1]["content"]) == {
                "raw_input": CASES[3]["raw_input"]
            }
            output = facts
        else:
            assert body["model"] == "test-generation"
            assert json.loads(body["messages"][1]["content"]) == facts
            output = {
                "entries": [
                    {
                        "source_index": 0,
                        "type": "skill",
                        "title": "Python",
                        "content": "使用 Python。",
                    }
                ]
            }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            },
        )

    settings.ai_mode = "live"
    settings.judge_model, settings.generation_model = "test-judge", "test-generation"
    settings.api_key = SecretStr("test-key")
    with TestClient(create_app(settings)) as client:
        monkeypatch.setattr(
            httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(provider), **kw)
        )
        result = normalize(client, save(client))
        nodes = client.get("/api/debug/runs/" + result["run_id"]).json()["nodes"]
        assert len(requests) == 2
        for index, name in [(1, "test-generation"), (3, "test-generation")]:
            assert nodes[index]["ai"]["model_name"] == name
            assert nodes[index]["ai"]["provider"] == "openai-compatible"
            assert nodes[index]["ai"]["total_tokens"] == 30
            assert nodes[index]["ai"]["retry_count"] == 0


def test_unconfirmed_invalid_edits_and_missing_profile(client):
    assert client.post("/api/profile/normalize", json={"expected_version": 0}).status_code == 404
    assert (
        client.post("/api/profile/raw", json={"expected_version": 0, "raw_input": "  "}).status_code
        == 422
    )
    profile = normalize(client, save(client))["profile"]
    edited = json.loads(json.dumps(profile["effective_profile"]))
    edited["entries"][0]["source_index"] = 8
    assert (
        client.put("/api/profile", json={"expected_version": 2, "profile": edited}).status_code
        == 422
    )
    assert client.get("/api/profile").json()["profile"] == profile
