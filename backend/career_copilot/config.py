from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseModel):
    role: Literal["judge", "generation"]
    model: str
    base_url: str
    api_key: SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ai_mode: Literal["fake", "replay", "live"] = "fake"
    app_env: Literal["development", "production", "test"] = "development"
    ai_debug_payloads: bool = False
    database_path: Path = Path("data/career-copilot.sqlite3")
    replay_dir: Path = Path("backend/fixtures/replay")
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    judge_model: str = ""
    judge_base_url: str = ""
    judge_api_key: SecretStr = SecretStr("")
    generation_model: str = ""
    generation_base_url: str = ""
    generation_api_key: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_live(self):
        if self.ai_mode == "live":
            for role in ("judge", "generation"):
                cfg = self.for_role(role)
                url = urlsplit(cfg.base_url)
                if not cfg.model or not cfg.api_key.get_secret_value():
                    raise ValueError(f"Live {role} requires MODEL and API_KEY configuration")
                if url.scheme not in {"http", "https"} or not url.netloc:
                    raise ValueError(f"Invalid {role} BASE_URL")
        return self

    @property
    def debug_enabled(self) -> bool:
        return self.app_env == "development"

    @property
    def payloads_enabled(self) -> bool:
        return self.debug_enabled and self.ai_debug_payloads

    def for_role(self, role: Literal["judge", "generation"]) -> ModelConfig:
        return ModelConfig(
            role=role,
            model=getattr(self, f"{role}_model") or self.model,
            base_url=getattr(self, f"{role}_base_url") or self.base_url,
            api_key=(
                getattr(self, f"{role}_api_key")
                if getattr(self, f"{role}_api_key").get_secret_value()
                else self.api_key
            ),
        )

    def secrets(self) -> list[str]:
        return [
            value.get_secret_value()
            for value in (self.api_key, self.judge_api_key, self.generation_api_key)
            if value.get_secret_value()
        ]
