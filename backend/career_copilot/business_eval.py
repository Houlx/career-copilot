"""Isolated synthetic task evaluation; no workflow or persistence access."""

from .config import Settings
from .models.prompts import PROMPTS
from .models.router import ModelRouter


def evaluation_model(settings: Settings, task: str, *, live: bool):
    if live and settings.ai_mode != "live":
        raise ValueError("Requires both --live and AI_MODE=live")
    router = ModelRouter(settings, replay_only=not live)
    return getattr(router, f"{PROMPTS[task].role}_model")


def announce(model, suite: str, case_count: int, *, live: bool):
    import json

    print(
        json.dumps(
            {
                "provider": model.config.provider if live else "replay",
                "model": model.config.model if live else "replay",
                "suite": suite,
                "case_count": case_count,
                "planned_request_count": 1 if live else 0,
            }
        ),
        flush=True,
    )


def failure_report(exc, calls, *, live):
    from .errors import ErrorCode

    return {
        "error_code": exc.code.value,
        "request_success": bool(calls) and exc.code != ErrorCode.MODEL_REQUEST_FAILED,
        "schema_mapping_success": False,
        "live_api_calls": sum(1 + call["retry_count"] for call in calls) if live else 0,
        "calls": calls,
    }
