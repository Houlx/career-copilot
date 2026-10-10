import hashlib
import json
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ..config import ModelConfig, Settings
from ..errors import CopilotError, ErrorCode
from .jobs import JobExtraction, Matching, fake_matching
from .profile import NormalizedProfile, ProfileFacts
from .prompts import PROMPTS, DiagnosticResult, JudgeDiagnosticResult, PromptSpec
from .providers import JevProvider, OpenAICompatibleProvider

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


class RoleModel:
    def __init__(self, config: ModelConfig, mode: str, replay_dir: Path):
        self.config = config
        self.mode = mode
        self.replay_dir = replay_dir

    def invoke(
        self, task: str, payload: dict, schema: type[T], *, max_attempts: int = 2
    ) -> ModelResult[T]:
        if max_attempts not in (1, 2):
            raise ValueError("Only one or two attempts are supported")
        spec = PROMPTS[task]
        if spec.role != self.config.role:
            raise ValueError("Task role does not match model role")
        started = perf_counter()
        meta = {
            "model_role": self.config.role,
            "provider": (
                self.config.provider.replace("_", "-") if self.mode == "live" else self.mode
            ),
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
                elif task == "judge-diagnostic-v1" and schema is JudgeDiagnosticResult:
                    value = {"decision": True}
                elif task == "generation-diagnostic-v1" and schema is DiagnosticResult:
                    value = {"message": "ok"}
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
                elif task == "jd-extract-v1" and schema is JobExtraction:
                    # Synthetic output, not an extraction quality simulator.
                    value = {
                        "title": "Synthetic demo job",
                        "requirements": [
                            {
                                "text": "Python",
                                "importance": "required",
                                "category": "skill",
                                "scoreability": "SCORED",
                            },
                            {
                                "text": "SQL",
                                "importance": "preferred",
                                "category": "skill",
                                "scoreability": "SCORED",
                            },
                            {
                                "text": "接受出差",
                                "importance": "required",
                                "category": "logistics",
                                "scoreability": "NEEDS_CONFIRMATION",
                            },
                            {
                                "text": "身体健康",
                                "importance": "required",
                                "category": "personal_trait",
                                "scoreability": "NON_SCORABLE",
                            },
                        ],
                    }
                elif task == "matching-v1" and schema is Matching:
                    value = fake_matching(payload)
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
                output = self._live(spec, payload, schema, meta, max_attempts)
            else:
                raise ValueError("Unknown AI mode")
            if payload_observer:
                payload_observer({"output": output.model_dump()})
            meta["latency_ms"] = round((perf_counter() - started) * 1000, 3)
            return ModelResult(output=output, metadata=meta)
        except (ValidationError, KeyError, TypeError) as exc:
            raise CopilotError(ErrorCode.MODEL_OUTPUT_INVALID) from exc
        finally:
            if meta["latency_ms"] == 0.0:
                meta["latency_ms"] = round((perf_counter() - started) * 1000, 3)
            observer = AI_OBSERVER.get()
            if observer:
                observer(meta.copy())

    def _live(
        self, spec: PromptSpec, payload: dict, schema: type[T], meta: dict, max_attempts: int
    ) -> T:
        # Instantiate adapters only on the live path. One retry owner; no SDK retries.
        provider = (
            JevProvider(self.config)
            if self.config.provider == "jev"
            else OpenAICompatibleProvider(self.config)
        )
        provider.validate_task(spec, schema)
        for attempt in range(max_attempts):
            meta["retry_count"] = attempt
            try:
                return provider.invoke(spec, payload, schema, meta)
            except httpx.HTTPError as exc:
                failure = CopilotError(ErrorCode.MODEL_REQUEST_FAILED)
                cause = exc
                meta["provider_metadata"] = {"error_kind": type(exc).__name__}
                if isinstance(exc, httpx.HTTPStatusError):
                    meta["provider_metadata"]["http_status"] = exc.response.status_code
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
