import hashlib
import json
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import ModelConfig, Settings
from ..errors import CopilotError, ErrorCode
from .profile import NormalizedProfile, ProfileFacts
from .prompts import PROMPTS, DiagnosticResult, PromptSpec

T = TypeVar("T", bound=BaseModel)
AI_OBSERVER: ContextVar[Callable[[dict], None] | None] = ContextVar("ai_observer", default=None)


AI_PAYLOAD_OBSERVER: ContextVar[Callable[[dict], None] | None] = ContextVar(
    "ai_payload_observer", default=None
)


class ModelResult[T: BaseModel](BaseModel):
    output: T
    metadata: dict[str, Any]


def replay_key(task: str, version: str, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"{task}--{version}--{digest}.json"


class ReplayRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str
    prompt_version: str
    output: dict


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0, strict=True)
    completion_tokens: int = Field(default=0, ge=0, strict=True)


class RoleModel:
    def __init__(self, config: ModelConfig, mode: str, replay_dir: Path):
        self.config = config
        self.mode = mode
        self.replay_dir = replay_dir

    def invoke(self, task: str, payload: dict, schema: type[T]) -> ModelResult[T]:
        spec = PROMPTS[task]
        if spec.role != self.config.role:
            raise ValueError("Task role does not match model role")
        started = perf_counter()
        meta = {
            "model_role": self.config.role,
            "provider": "openai-compatible" if self.mode == "live" else self.mode,
            "model_name": self.config.model if self.mode == "live" else self.mode,
            "prompt_version": spec.version,
            "temperature": spec.temperature,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency_ms": 0.0,
            "retry_count": 0,
        }
        payload_observer = AI_PAYLOAD_OBSERVER.get()
        if payload_observer:
            payload_observer({"prompt": spec.system, "input": payload})
        try:
            if self.mode == "fake":
                # Offline identity fixtures: preserve supplied text, never invent user facts.
                if task == "diagnostic" and schema is DiagnosticResult:
                    value = {"message": "Provider connection verified"}
                elif task == "profile-facts-v1" and schema is ProfileFacts:
                    raw = payload["raw_input"]
                    value = {
                        "facts": [
                            {
                                "type": "other",
                                "title": "原始资料",
                                "content": raw,
                                "source_quote": raw,
                            }
                        ]
                    }
                elif task == "profile-normalize-v1" and schema is NormalizedProfile:
                    facts = ProfileFacts.model_validate(payload)
                    value = {
                        "entries": [
                            {
                                "source_index": i,
                                "type": f.type,
                                "title": f.title,
                                "content": f.content,
                            }
                            for i, f in enumerate(facts.facts)
                        ]
                    }
                else:
                    raise CopilotError(ErrorCode.MODEL_OUTPUT_INVALID)
                output = schema.model_validate(value)
            elif self.mode == "replay":
                path = self.replay_dir / replay_key(task, spec.version, payload)
                try:
                    record = ReplayRecord.model_validate_json(path.read_text())
                except FileNotFoundError as exc:
                    raise CopilotError(ErrorCode.REPLAY_NOT_FOUND) from exc
                except (ValueError, OSError) as exc:
                    raise CopilotError(ErrorCode.MODEL_OUTPUT_INVALID) from exc
                if record.task != task or record.prompt_version != spec.version:
                    raise CopilotError(ErrorCode.MODEL_OUTPUT_INVALID)
                output = schema.model_validate(record.output)
            elif self.mode == "live":
                output = self._live(spec, payload, schema, meta)
            else:
                raise ValueError("Unknown AI mode")
            if payload_observer:
                payload_observer({"output": output.model_dump()})
            meta["latency_ms"] = round((perf_counter() - started) * 1000, 3)
            return ModelResult(output=output, metadata=meta)
        except (ValidationError, KeyError, TypeError) as exc:
            raise CopilotError(ErrorCode.MODEL_OUTPUT_INVALID) from exc
        finally:
            meta["latency_ms"] = round((perf_counter() - started) * 1000, 3)
            observer = AI_OBSERVER.get()
            if observer:
                observer(meta.copy())

    def _live(self, spec: PromptSpec, payload: dict, schema: type[T], meta: dict) -> T:
        # At most two attempts, no repair conversation or hidden automatic SDK retries.
        for attempt in range(2):
            meta["retry_count"] = attempt
            try:
                with httpx.Client(timeout=30, follow_redirects=False) as client:
                    response = client.post(
                        f"{self.config.base_url.rstrip('/')}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.config.api_key.get_secret_value()}"
                        },
                        json={
                            "model": self.config.model,
                            "messages": [
                                {"role": "system", "content": spec.system},
                                {
                                    "role": "user",
                                    "content": json.dumps(payload, ensure_ascii=False),
                                },
                            ],
                            "temperature": spec.temperature,
                            "max_tokens": spec.max_output_tokens,
                            "response_format": {
                                "type": "json_schema",
                                "json_schema": {
                                    "name": schema.__name__,
                                    "strict": True,
                                    "schema": schema.model_json_schema(),
                                },
                            },
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("Expected a structured provider response")
                    usage = TokenUsage.model_validate(body.get("usage") or {})
                    meta["input_tokens"] += usage.prompt_tokens
                    meta["output_tokens"] += usage.completion_tokens
                    meta["total_tokens"] = meta["input_tokens"] + meta["output_tokens"]
                    return schema.model_validate_json(body["choices"][0]["message"]["content"])
            except httpx.HTTPError as exc:
                failure = CopilotError(ErrorCode.MODEL_REQUEST_FAILED)
                cause = exc
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                failure = CopilotError(ErrorCode.MODEL_OUTPUT_INVALID)
                cause = exc
        raise failure from cause


class ModelRouter:
    def __init__(self, settings: Settings, *, replay_only: bool = False):
        mode = "replay" if replay_only else settings.ai_mode
        self.judge_model = RoleModel(settings.for_role("judge"), mode, settings.replay_dir)
        self.generation_model = RoleModel(
            settings.for_role("generation"), mode, settings.replay_dir
        )
