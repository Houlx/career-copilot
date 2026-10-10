"""Explicit synthetic diagnostics, with a double live gate and one request per run."""

import argparse
import json
from types import SimpleNamespace

from pydantic import ValidationError

from .config import Settings
from .database import Database
from .errors import CopilotError, ErrorCode
from .models.prompts import DiagnosticResult, JudgeDiagnosticResult
from .models.router import ModelRouter
from .tracing import RunTrace, redact


def run_smoke(settings: Settings, role: str, *, live: bool = False) -> dict:
    # Check before constructing a router, client or database.
    if not live or settings.ai_mode != "live":
        raise ValueError("Requires both --live and AI_MODE=live")
    if role not in ("judge", "generation"):
        raise ValueError("Unknown diagnostic role")
    settings.validate_live()
    router = ModelRouter(settings)
    task = f"{role}-diagnostic-v1"
    payload = {"state": "Synthetic candidate has Python and SQL experience."}
    schema = JudgeDiagnosticResult if role == "judge" else DiagnosticResult
    model = getattr(router, f"{role}_model")
    db = Database(settings.database_path)
    db.initialize()
    trace = RunTrace(db, settings, "provider_diagnostic", task)

    def diagnostic(state):
        result = model.invoke(task, state, schema, max_attempts=1)
        expected = {"decision": True} if role == "judge" else {"message": "ok"}
        if result.output.model_dump() != expected:
            raise CopilotError(ErrorCode.MODEL_OUTPUT_INVALID)
        return result.model_dump()

    # Reuse the tracing lifecycle without invoking Profile/Job graphs or stores.
    node = trace.node(task, diagnostic)
    result = trace.execute(SimpleNamespace(invoke=node), payload)
    return redact({"run_id": trace.run_id, **result}, settings.secrets())


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Authorize one real billable request")
    parser.add_argument("--role", choices=("judge", "generation"), required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("Requires both --live and AI_MODE=live")
    try:
        settings = Settings()
    except ValidationError as exc:
        # Pydantic's default exception text includes input values, potentially secrets.
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        parser.error(
            "Invalid live configuration; check model, base URL and API key for both "
            "roles" + (f" (fields: {', '.join(fields)})" if fields else "")
        )
    if settings.ai_mode != "live":
        parser.error("Requires both --live and AI_MODE=live")
    try:
        result = run_smoke(settings, args.role, live=args.live)
    except CopilotError as exc:
        print(json.dumps({"error_code": exc.code.value, "run_id": getattr(exc, "run_id", None)}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
