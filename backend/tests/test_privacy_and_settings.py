import json

import pytest
from career_copilot.api import create_app
from career_copilot.config import Settings
from career_copilot.tracing import redact, state_diff
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError


def test_default_mode_and_role_fallback():
    settings = Settings(_env_file=None, model="shared", api_key="shared-key", judge_model="judge")
    assert settings.ai_mode == "fake"
    assert not settings.payloads_enabled
    assert settings.for_role("judge").model == "judge"
    assert settings.for_role("generation").model == "shared"
    assert settings.for_role("judge").api_key.get_secret_value() == "shared-key"
    assert "shared-key" not in repr(settings)


def test_live_requires_explicit_credentials():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_mode="live")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_mode="unknown")


def test_role_overrides():
    settings = Settings(
        _env_file=None,
        model="shared",
        api_key="shared-key",
        generation_model="writer",
        generation_base_url="http://localhost:1234/v1",
        generation_api_key="writer-key",
    )
    cfg = settings.for_role("generation")
    assert cfg.model == "writer"
    assert cfg.base_url == "http://localhost:1234/v1"
    assert cfg.api_key.get_secret_value() == "writer-key"


def test_settings_endpoint_never_exposes_credentials(settings):
    settings.generation_timeout_seconds = 60
    settings.judge_timeout_seconds = 25
    settings.api_key = SecretStr("special-secret")
    settings.base_url = "https://user:password@example.com/v1?api_key=special-secret"
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/settings")
        assert response.status_code == 200
        assert "special-secret" not in response.text
        assert "password" not in response.text
        assert response.json()["models"][0]["api_key_configured"]
        models = {model["role"]: model for model in response.json()["models"]}
        assert models["generation"]["timeout_seconds"] == 60
        assert models["judge"]["timeout_seconds"] == 25


def test_debug_payload_opt_in_and_secret_redaction(settings):
    settings.ai_debug_payloads = True
    settings.api_key = SecretStr("test-configured-secret")
    with TestClient(create_app(settings)) as client:
        run_id = client.post(
            "/api/workflows/dummy",
            json={
                "message": "private career text test-configured-secret sk-other-secret Bearer foo",
            },
        ).json()["run_id"]
        run = client.get(f"/api/debug/runs/{run_id}").json()
        text = json.dumps(run)
        assert "private career text" in text
        assert "test-configured-secret" not in text
        assert "sk-other-secret" not in text
        assert "Bearer foo" not in text
        assert "[REDACTED]" in text
    # Disabling the flag also prevents reading payloads previously persisted with opt-in.
    settings.ai_debug_payloads = False
    with TestClient(create_app(settings)) as client:
        run = client.get(f"/api/debug/runs/{run_id}").json()
        assert all("debug_payload" not in n for n in run["nodes"])


def test_production_hides_debug_apis_even_with_payload_flag(settings):
    settings.app_env = "production"
    settings.ai_debug_payloads = True
    assert not settings.payloads_enabled
    with TestClient(create_app(settings)) as client:
        assert not client.get("/api/settings").json()["debug_enabled"]
        for path in ("/api/debug/runs", "/api/debug/runs/id"):
            assert client.get(path).status_code == 404
        assert client.post("/api/debug/replay", json={}).status_code == 404
        assert client.post("/api/workflows/dummy", json={}).status_code == 200


def test_redaction_and_diff_only_changed_fields():
    assert redact({"API_KEY": "abc", "nested": {"Authorization": "abc"}}, []) == {
        "API_KEY": "[REDACTED]",
        "nested": {"Authorization": "[REDACTED]"},
    }
    diff = state_diff({"same": 1, "text": "personal"}, {"same": 1, "text": "changed", "n": 2})
    assert set(diff) == {"text", "n"}
    assert "personal" not in json.dumps(diff)


def test_structured_output_default_override_and_settings_privacy(settings, monkeypatch):
    monkeypatch.delenv("GENERATION_STRUCTURED_OUTPUT", raising=False)
    assert Settings(_env_file=None).for_role("generation").structured_output_mode == "json_schema"
    monkeypatch.setenv("GENERATION_STRUCTURED_OUTPUT", "json_object")
    assert Settings(_env_file=None).for_role("generation").structured_output_mode == "json_object"
    settings.generation_structured_output = "json_object"
    settings.judge_provider = "jev"
    settings.generation_api_key = SecretStr("private-generation-key")
    settings.generation_base_url = "https://user:password@example.com"
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/settings")
    models = {model["role"]: model for model in response.json()["models"]}
    assert models["generation"]["structured_output_mode"] == "json_object"
    assert models["judge"]["structured_output_mode"] is None
    assert "private-generation-key" not in response.text
    assert "password" not in response.text and "example.com" not in response.text


@pytest.mark.parametrize("value", ["text", "auto", "invalid"])
def test_structured_output_mode_rejects_unknown_capability(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_structured_output=value)


def test_timeout_defaults_and_role_environment_overrides(monkeypatch):
    defaults = Settings(_env_file=None)
    assert defaults.for_role("generation").timeout_seconds == 30
    assert defaults.for_role("judge").timeout_seconds == 30
    monkeypatch.setenv("GENERATION_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("JUDGE_TIMEOUT_SECONDS", "25")
    settings = Settings(_env_file=None, judge_provider="jev")
    assert settings.for_role("generation").timeout_seconds == 60
    assert settings.for_role("judge").timeout_seconds == 25
    assert (
        Settings(_env_file=None, judge_provider="openai_compatible")
        .for_role("judge")
        .timeout_seconds
        == 25
    )


@pytest.mark.parametrize("role", ["generation", "judge"])
@pytest.mark.parametrize("value", ["0", "-1", "301", "nan", "inf"])
def test_invalid_timeout_environment_rejected_at_startup(monkeypatch, role, value):
    monkeypatch.setenv(f"{role.upper()}_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert error.value.errors()[0]["loc"] == (f"{role}_timeout_seconds",)
