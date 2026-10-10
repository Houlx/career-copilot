"""Synthetic GLM contract failures, with no real sockets or response-body retention."""

import json

import httpx
import pytest
from career_copilot.config import Settings
from career_copilot.errors import CopilotError, ErrorCode
from career_copilot.models.diagnostics import DIAGNOSTICS_ENABLED
from career_copilot.models.profile import ProfileFacts, validate_facts
from career_copilot.models.router import AI_OBSERVER, ModelRouter

FACT = {
    "type": "education",
    "title": "Education",
    "content": "Synthetic University",
    "source_quote": "Synthetic University",
}


def invoke(monkeypatch, body, *, debug=True, env="development", raw=False, mode="json_schema"):
    real_client = httpx.Client
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, **({"text": body} if raw else {"json": body}))

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    settings = Settings(
        _env_file=None,
        ai_mode="live",
        model="glm-5.3-flash",
        api_key="synthetic-secret",
        ai_debug_payloads=debug,
        app_env=env,
        generation_structured_output=mode,
    )
    calls = []
    token = AI_OBSERVER.set(calls.append)
    try:
        try:
            output = (
                ModelRouter(settings)
                .generation_model.invoke(
                    "profile-facts-v1",
                    {"raw_input": "Synthetic University"},
                    ProfileFacts,
                    max_attempts=1,
                )
                .output
            )
        except CopilotError as exc:
            assert exc.code == ErrorCode.MODEL_OUTPUT_INVALID
            output = None
    finally:
        AI_OBSERVER.reset(token)
    assert len(requests) == 1 and calls[0]["retry_count"] == 0
    assert calls[0]["structured_output_mode"] == mode
    if mode == "json_object":
        assert requests[0]["response_format"] == {"type": "json_object"}
    else:
        assert requests[0]["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "ProfileFacts",
                "strict": True,
                "schema": ProfileFacts.model_json_schema(),
            },
        }
    return output, calls[0]


def envelope(content):
    return {
        "choices": [
            {"message": {"content": content, "reasoning_content": "DO NOT SAVE REASONING"}}
        ],
        "usage": {"prompt_tokens": 113, "completion_tokens": 243},
    }


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
def test_profile_contract_success(monkeypatch, mode):
    output, meta = invoke(monkeypatch, envelope(json.dumps({"facts": [FACT]})), mode=mode)
    assert output == ProfileFacts.model_validate({"facts": [FACT]})
    assert "failure_stage" not in meta and meta["total_tokens"] == 356


@pytest.mark.parametrize(
    "fact,path,kind",
    [
        ({k: v for k, v in FACT.items() if k != "source_quote"}, "source_quote", "missing"),
        ({**FACT, "extra": "private-career-text"}, "extra", "extra_forbidden"),
        ({**FACT, "type": "invented"}, "type", "literal_error"),
        ({**FACT, "content": 7}, "content", "string_type"),
    ],
)
@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
def test_schema_failure_path_and_privacy(monkeypatch, fact, path, kind, mode):
    output, meta = invoke(monkeypatch, envelope(json.dumps({"facts": [fact]})), mode=mode)
    assert output is None and meta["failure_stage"] == "PYDANTIC_VALIDATION_FAILED"
    assert meta["errors"][0]["loc"] == ["facts", 0, path]
    assert meta["errors"][0]["type"] == kind
    saved = json.dumps(meta)
    for value in ["private-career-text", "Synthetic University", "DO NOT SAVE REASONING"]:
        assert value not in saved
    assert meta["total_tokens"] == 356


@pytest.mark.parametrize("content", ["```json\n{}\n```", "not json", "{"])
@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
def test_structured_json_failure_without_repair(monkeypatch, content, mode):
    output, meta = invoke(monkeypatch, envelope(content), mode=mode)
    assert output is None and meta["failure_stage"] == "STRUCTURED_JSON_INVALID"


@pytest.mark.parametrize(
    "body,raw",
    [
        ({"choices": []}, False),
        ({"output": {"facts": [FACT]}}, False),
        (envelope({"facts": [FACT]}), False),
        (envelope(None), False),
        ("not provider JSON", True),
        ([], False),
        ({"choices": [{"message": {"parsed": {"facts": [FACT]}}}]}, False),
        ({"usage": {"prompt_tokens": "wrong"}}, False),
    ],
)
@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
def test_envelope_failure(monkeypatch, body, raw, mode):
    output, meta = invoke(monkeypatch, body, raw=raw, mode=mode)
    assert output is None and meta["failure_stage"] == "RESPONSE_ENVELOPE_INVALID"


@pytest.mark.parametrize(
    "debug,env", [(False, "development"), (True, "production"), (True, "test")]
)
def test_diagnostic_details_require_both_gates(monkeypatch, debug, env):
    _, meta = invoke(monkeypatch, envelope('{"facts":[{}]}'), debug=debug, env=env)
    assert meta["failure_stage"] == "PYDANTIC_VALIDATION_FAILED"
    assert "errors" not in meta and "structured_output_shape" not in meta


@pytest.mark.parametrize(
    "fact,rule,path",
    [
        ({**FACT, "source_quote": "Absent"}, "source_quote_contiguous_excerpt", "source_quote"),
        ({**FACT, "content": "Synthetic University 2026"}, "numeric_grounding", "content"),
    ],
)
def test_business_validation_preserves_rules(fact, rule, path):
    token = DIAGNOSTICS_ENABLED.set(True)
    try:
        with pytest.raises(CopilotError) as error:
            validate_facts(ProfileFacts.model_validate({"facts": [fact]}), "Synthetic University")
    finally:
        DIAGNOSTICS_ENABLED.reset(token)
    assert error.value.code == ErrorCode.PROFILE_VALIDATION_FAILED
    assert error.value.diagnostics["failure_stage"] == "BUSINESS_VALIDATION_FAILED"
    assert error.value.diagnostics["errors"][0]["loc"] == ["facts", 0, path]
    assert error.value.diagnostics["errors"][0]["type"] == rule


def test_business_eval_reports_successful_schema_before_grounding_failure():
    from career_copilot.business_eval import failure_report

    token = DIAGNOSTICS_ENABLED.set(True)
    try:
        with pytest.raises(CopilotError) as error:
            validate_facts(ProfileFacts.model_validate({"facts": [FACT]}), "Different input")
    finally:
        DIAGNOSTICS_ENABLED.reset(token)
    report = failure_report(error.value, [{"retry_count": 0}], live=True)
    assert report["failure_stage"] == "BUSINESS_VALIDATION_FAILED"
    assert report["schema_mapping_success"] is True and report["live_api_calls"] == 1


@pytest.mark.parametrize("debug", [True, False])
def test_business_failure_trace_and_public_error_contract(settings, debug):
    from career_copilot.database import Database
    from career_copilot.tracing import RunTrace

    settings.ai_debug_payloads = debug
    db = Database(settings.database_path)
    db.initialize()
    trace = RunTrace(db, settings, "diagnostic-test", "v1")
    db.start_run(
        {
            "run_id": trace.run_id,
            "workflow_name": "diagnostic-test",
            "workflow_version": "v1",
            "started_at": "2026-10-10T00:00:00Z",
            "status": "running",
        }
    )

    def validate(state):
        validate_facts(ProfileFacts.model_validate({"facts": [FACT]}), "Different input")

    with pytest.raises(CopilotError) as error:
        trace.node("validate_facts", validate)({})
    assert str(error.value) == "PROFILE_VALIDATION_FAILED"
    validation = db.get_run(trace.run_id)["nodes"][0]["validation_result"]
    assert validation["failure_stage"] == "BUSINESS_VALIDATION_FAILED"
    assert ("errors" in validation) is debug
    assert "Synthetic University" not in json.dumps(validation)


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
@pytest.mark.parametrize(
    "fact,rule",
    [
        ({**FACT, "source_quote": "Absent"}, "source_quote_contiguous_excerpt"),
        ({**FACT, "content": "Synthetic University 2026"}, "numeric_grounding"),
    ],
)
def test_both_wire_modes_preserve_business_validation(monkeypatch, mode, fact, rule):
    output, meta = invoke(monkeypatch, envelope(json.dumps({"facts": [fact]})), mode=mode)
    assert output is not None and "failure_stage" not in meta
    token = DIAGNOSTICS_ENABLED.set(True)
    try:
        with pytest.raises(CopilotError) as error:
            validate_facts(output, "Synthetic University")
    finally:
        DIAGNOSTICS_ENABLED.reset(token)
    assert error.value.code == ErrorCode.PROFILE_VALIDATION_FAILED
    assert error.value.diagnostics["errors"][0]["type"] == rule
