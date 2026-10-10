import re
from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from .config import Settings
from .database import Database
from .errors import CopilotError, ErrorCode, WorkflowError
from .models.diagnostics import DIAGNOSTICS_ENABLED
from .models.router import AI_OBSERVER, AI_PAYLOAD_OBSERVER


def now() -> str:
    return datetime.now(UTC).isoformat()


def summary(value):
    """Shape-only summaries: no career text, even in error paths."""
    if isinstance(value, dict):
        return {key: summary(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return {"type": "list", "count": len(value)}
    if isinstance(value, str):
        return {"type": "str", "length": len(value)}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return {"type": type(value).__name__}


def state_diff(before: dict, updates: dict) -> dict:
    return {
        key: {"before": summary(before.get(key)), "after": summary(value)}
        for key, value in updates.items()
        if key not in before or before[key] != value
    }


def redact(value, secrets: list[str]):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"api.?key|authorization|secret|password|access.?token", key, re.I)
            else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
        return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
    return value


class RunTrace:
    def __init__(self, db: Database, settings: Settings, workflow_name: str, version: str):
        self.db = db
        self.settings = settings
        self.run_id = str(uuid4())
        self.workflow_name = workflow_name
        self.version = version
        self.sequence = 0

    def node(self, name: str, fn: Callable, failure_code=ErrorCode.WORKFLOW_FAILED):
        def wrapped(state: dict) -> dict:
            self.sequence += 1
            started = perf_counter()
            trace = {
                "run_id": self.run_id,
                "node_name": name,
                "sequence": self.sequence,
                "started_at": now(),
                "duration_ms": 0,
                "status": "running",
                "input_summary": summary(state),
                "output_summary": {},
                "state_changes": {},
                "validation_result": None,
                "error_code": None,
                "ai": None,
            }
            ai_payload = {}
            diagnostics_token = DIAGNOSTICS_ENABLED.set(self.settings.payloads_enabled)
            token = AI_OBSERVER.set(lambda meta: trace.update(ai=meta))
            payload_token = AI_PAYLOAD_OBSERVER.set(
                ai_payload.update if self.settings.payloads_enabled else None
            )
            try:
                updates = fn(state)
                if not isinstance(updates, dict):
                    raise CopilotError(failure_code)
                trace.update(
                    status="completed",
                    output_summary=summary(updates),
                    state_changes=state_diff(state, updates),
                    validation_result={"status": "passed", "check": "node_output_is_mapping"},
                )
                if self.settings.payloads_enabled:
                    trace["debug_payload"] = {
                        "input": state,
                        "output": updates,
                        "model": ai_payload,
                    }
                return updates
            except Exception as exc:
                code = exc.code if isinstance(exc, CopilotError) else failure_code
                trace.update(
                    status="failed",
                    error_code=code.value,
                    validation_result={"status": "failed", "error_code": code.value},
                )
                trace["validation_result"].update(getattr(exc, "diagnostics", {}))
                if self.settings.payloads_enabled:
                    # Exception messages may contain credentials or personal data.
                    trace["debug_payload"] = {
                        "input": state,
                        "model": ai_payload,
                        "error_code": code.value,
                    }
                raise CopilotError(code) from exc
            finally:
                DIAGNOSTICS_ENABLED.reset(diagnostics_token)
                AI_OBSERVER.reset(token)
                AI_PAYLOAD_OBSERVER.reset(payload_token)
                trace["duration_ms"] = round((perf_counter() - started) * 1000, 3)
                self.db.add_node(redact(trace, self.settings.secrets()))

        return wrapped

    def execute(self, graph, initial: dict) -> dict:
        started = perf_counter()
        self.db.start_run(
            {
                "run_id": self.run_id,
                "workflow_name": self.workflow_name,
                "workflow_version": self.version,
                "started_at": now(),
                "status": "running",
            }
        )
        try:
            result = graph.invoke(initial)
        except Exception as exc:
            code = exc.code if isinstance(exc, CopilotError) else ErrorCode.WORKFLOW_FAILED
            self.db.finish_run(
                self.run_id, now(), (perf_counter() - started) * 1000, "failed", code.value
            )
            raise WorkflowError(code, self.run_id) from exc
        self.db.finish_run(self.run_id, now(), (perf_counter() - started) * 1000, "completed", None)
        return result
