from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .database import Database
from .errors import CopilotError, ErrorCode, WorkflowError
from .models.prompts import PROMPTS, DiagnosticResult
from .models.router import ModelRouter
from .tracing import RunTrace
from .workflows.dummy import build_dummy_graph


class DummyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="Career Copilot", min_length=1, max_length=500)


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = "diagnostic"
    payload: dict = Field(default_factory=dict)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.database_path)
    router = ModelRouter(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.initialize()
        yield

    app = FastAPI(title="Career Copilot", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.models = router

    def development_only():
        if not settings.debug_enabled:
            raise HTTPException(status_code=404, detail="Not found")

    @app.exception_handler(CopilotError)
    async def copilot_error_handler(request, exc: CopilotError):
        body = {"error_code": exc.code.value, "message": "操作失败，请查看运行记录。"}
        if isinstance(exc, WorkflowError):
            body["run_id"] = exc.run_id
        status = 404 if exc.code == ErrorCode.REPLAY_NOT_FOUND else 500
        return JSONResponse(status_code=status, content=body)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "phase": 1}

    @app.get("/api/settings")
    def read_settings():
        # Never serialize Settings or credentials. URLs are not returned either.
        return {
            "ai_mode": settings.ai_mode,
            "app_env": settings.app_env,
            "debug_enabled": settings.debug_enabled,
            "debug_payloads": settings.payloads_enabled,
            "models": [
                {
                    "role": role,
                    "provider": "openai-compatible",
                    "model": settings.for_role(role).model or "Not configured",
                    "api_key_configured": bool(settings.for_role(role).api_key.get_secret_value()),
                }
                for role in ("judge", "generation")
            ],
            "prompts": {task: spec.version for task, spec in PROMPTS.items()},
        }

    @app.post("/api/workflows/dummy")
    def run_dummy(body: DummyRequest):
        trace = RunTrace(db, settings, "dummy", "dummy-v1")
        state = trace.execute(
            build_dummy_graph(trace),
            {
                "message": body.message,
                "counter": 0,
                "result": "",
            },
        )
        return {"run_id": trace.run_id, "state": state}

    debug_deps = [Depends(development_only)]

    @app.get("/debug/runs", dependencies=debug_deps, include_in_schema=False)
    @app.get("/api/debug/runs", dependencies=debug_deps)
    def list_runs(
        limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0
    ):
        return {"runs": db.list_runs(limit, offset)}

    @app.get("/debug/runs/{run_id}", dependencies=debug_deps, include_in_schema=False)
    @app.get("/api/debug/runs/{run_id}", dependencies=debug_deps)
    def get_run(run_id: str):
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if not settings.payloads_enabled:
            for node in run["nodes"]:
                node.pop("debug_payload", None)
        return run

    @app.post("/api/debug/replay", dependencies=debug_deps)
    def replay_node(body: ReplayRequest):
        # Diagnostic only in Phase 1. Forced replay even when AI_MODE=live.
        # No workflow or business persistence: independent input → validated output.
        if body.task != "diagnostic":
            raise HTTPException(status_code=422, detail="Unsupported Phase 1 task")
        replay_router = ModelRouter(settings, replay_only=True)
        return replay_router.judge_model.invoke(
            body.task, body.payload, DiagnosticResult
        ).model_dump()

    return app
