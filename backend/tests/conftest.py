import pytest
from career_copilot.api import create_app
from career_copilot.config import Settings
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_config_environment(monkeypatch):
    # Tests must not inherit an operator's live configuration or local .env.
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_path=tmp_path / "test.sqlite3")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client
