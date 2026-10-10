"""Document/renderer checks stay separate from semantic quality evaluation."""

import copy
import json

import pytest
from career_copilot.models.contracts import build_prompt_contract
from career_copilot.models.resumes import (
    LABELS,
    SECTION_ORDER,
    ResumeDocument,
    ResumeDraft,
    ResumeHeader,
    build_document,
    render_markdown,
    validate_grounding,
)
from career_copilot.models.revisions import ResumeRevisionProposal
from pydantic import ValidationError
from test_resumes import EVIDENCE, draft, generate, setup_job
from test_revisions import resolve, send, setup


@pytest.mark.parametrize("language", ["zh", "en", "ja"])
def test_renderer_owns_order_labels_and_empty_sections(language):
    value = draft()
    item = value["summary"][0]
    value.update(
        language=language,
        sections=[{"kind": kind, "items": [item]} for kind in reversed(SECTION_ORDER)],
    )
    document = build_document(ResumeDraft.model_validate(value), {"name": "张三"})
    markdown = render_markdown(document)
    assert markdown.splitlines()[0] == "# 张三"
    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings == ["## " + LABELS[language][kind] for kind in ("summary", *SECTION_ORDER)]
    document.summary = []
    document.sections[0].items = []
    markdown = render_markdown(document)
    assert "## " + LABELS[language]["summary"] not in markdown
    assert "## " + LABELS[language]["additional"] not in markdown


@pytest.mark.parametrize(
    "contacts,expected",
    [
        (
            {"phone": "123", "email": "a@example.test", "location": "上海"},
            "123 | a@example\\.test | 上海",
        ),
        ({"email": "a@example.test", "location": "上海"}, "a@example\\.test | 上海"),
        ({"phone": "123"}, "123"),
        ({}, None),
    ],
)
def test_header_has_no_evidence_or_empty_separators(contacts, expected):
    parsed = ResumeDraft.model_validate(draft())
    header = ResumeHeader(name="张三", **contacts)
    assert "evidence_ids" not in header.model_dump()
    validate_grounding(parsed, EVIDENCE, 3)
    markdown = render_markdown(build_document(parsed, header.model_dump()))
    assert markdown.startswith("# 张三\n\n")
    if expected:
        assert markdown.splitlines()[2] == expected
    else:
        assert " | " not in markdown


def test_provider_contract_excludes_document_metadata():
    for model in (ResumeDraft, ResumeRevisionProposal):
        contract = json.dumps(build_prompt_contract(model))
        assert '"header"' not in contract and '"headline"' not in contract
    value = draft()
    for key, metadata in (("header", {"name": "Fake"}), ("headline", value["summary"][0])):
        with pytest.raises(ValidationError):
            ResumeDraft.model_validate({**value, key: metadata})


def test_snapshot_old_artifact_and_new_profile(client, monkeypatch):
    job = setup_job(client)
    original = client.app.state.models.generation_model.invoke
    payloads = []

    def capture(task, payload, *args, **kwargs):
        payloads.append(copy.deepcopy(payload))
        return original(task, payload, *args, **kwargs)

    monkeypatch.setattr(client.app.state.models.generation_model, "invoke", capture)
    first = generate(client, job).json()["resume"]
    assert first["markdown"].startswith("# 张三\n\n123 | a@example\\.test | 上海\n")
    assert "a@example.test" not in json.dumps(payloads)
    assert "header" not in payloads[0] and "basic_info" not in payloads[0]
    trace = client.get("/api/debug/runs").json()
    assert "a@example.test" not in json.dumps(trace)
    profile = client.get("/api/profile").json()["profile"]
    edited = client.put(
        "/api/profile",
        json={
            "expected_version": profile["profile_version"],
            "profile": profile["effective_profile"],
            "basic_info": {"name": "张三", "phone": "456"},
        },
    ).json()["profile"]
    assert not edited["confirmed"]
    client.post(
        "/api/profile/confirm", json={"expected_version": edited["profile_version"]}
    ).raise_for_status()
    old = client.get("/api/resumes/" + first["id"]).json()["resume"]
    assert old["structured_resume"] == first["structured_resume"]
    assert old["markdown"] == first["markdown"] and old["outdated"]
    new_job = client.post("/api/jobs/analyze", json={"raw_jd": "Synthetic JD"}).json()["job"]["id"]
    second = generate(client, new_job).json()["resume"]
    assert second["structured_resume"]["header"]["phone"] == "456"
    assert second["markdown"].startswith("# 张三\n\n456\n")
    assert ResumeDocument.model_validate(second["structured_resume"])


def test_historical_headline_artifact_is_read_verbatim(client):
    job = setup_job(client)
    legacy = {
        **draft(),
        "headline": {"text": "Legacy professional headline", "evidence_ids": [EVIDENCE[0]["id"]]},
    }
    with client.app.state.db.connect() as conn:
        conn.execute(
            "INSERT INTO resumes VALUES (?,?,?,?,'completed',?,?,?,?)",
            (
                "legacy",
                job,
                4,
                "en",
                json.dumps(legacy),
                "# Saved legacy headline\n",
                "[]",
                "date",
            ),
        )
    artifact = client.get("/api/resumes/legacy").json()["resume"]
    assert artifact["structured_resume"] == legacy
    assert artifact["markdown"] == "# Saved legacy headline\n"


def test_revision_cannot_change_header_and_child_inherits_snapshot(client, monkeypatch):
    parent, expected, thread = setup(client)
    original = client.app.state.models.generation_model.invoke
    monkeypatch.setattr(
        client.app.state.models.generation_model,
        "invoke",
        lambda *a, **kw: pytest.fail("Identity edit called provider"),
    )
    response = send(client, thread, expected, "把电话换成 456")
    response.raise_for_status()
    assert response.json()["proposal_id"] is None
    assert "请先更新 Profile 基本信息" in response.json()["thread"]["messages"][-1]["content"]
    monkeypatch.setattr(client.app.state.models.generation_model, "invoke", original)
    proposal = send(client, thread, expected).json()["proposal_id"]
    child = resolve(client, proposal, expected).json()["resume"]
    assert child["structured_resume"]["header"] == parent["structured_resume"]["header"]
    assert child["markdown"].split("##")[0] == parent["markdown"].split("##")[0]


def test_missing_name_requires_profile_update_before_generation(client, monkeypatch):
    client.post(
        "/api/profile/raw", json={"raw_input": "Python tools", "expected_version": 0}
    ).raise_for_status()
    client.post("/api/profile/normalize", json={"expected_version": 1}).raise_for_status()
    client.post("/api/profile/confirm", json={"expected_version": 2}).raise_for_status()
    job = client.post("/api/jobs/analyze", json={"raw_jd": "Synthetic JD"}).json()["job"]["id"]
    monkeypatch.setattr(
        client.app.state.models.generation_model,
        "invoke",
        lambda *a: pytest.fail("Missing name reached model"),
    )
    response = generate(client, job)
    assert response.json()["error_code"] == "PROFILE_VALIDATION_FAILED"


def test_header_contacts_not_written_to_opt_in_trace(client, settings):
    settings.ai_debug_payloads = True
    job = setup_job(client)
    response = generate(client, job)
    response.raise_for_status()
    trace = client.get("/api/debug/runs/" + response.json()["run_id"]).json()
    assert any("debug_payload" in node for node in trace["nodes"])
    assert "a@example.test" not in json.dumps(trace)
    assert "a@example\\.test" not in json.dumps(trace)
