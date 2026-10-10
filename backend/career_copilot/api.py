from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .database import Database
from .errors import CopilotError, ErrorCode, WorkflowError
from .job_store import JobStore
from .models.jobs import JobExtraction, JobRequirements, Matching, ScoreabilityResult
from .models.profile import NormalizedProfile, ProfileFacts
from .models.prompts import PROMPTS, DiagnosticResult
from .models.router import ModelRouter
from .profile_store import ProfileStore
from .tracing import RunTrace
from .workflows.dummy import build_dummy_graph
from .workflows.jobs import build_job_graph
from .workflows.profile import build_profile_graph


class DummyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="Career Copilot", min_length=1, max_length=500)


class AnalyzeJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    raw_jd: str = Field(min_length=1, max_length=30000)


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = "diagnostic"
    payload: dict = Field(default_factory=dict)


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=0)


class RawProfileRequest(VersionRequest):
    raw_input: str = Field(min_length=1, max_length=20000)


class EditProfileRequest(VersionRequest):
    profile: NormalizedProfile


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.database_path)
    router = ModelRouter(settings)
    profiles = ProfileStore(db)
    jobs = JobStore(db)

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
        status = (
            404
            if exc.code in {ErrorCode.REPLAY_NOT_FOUND, ErrorCode.PROFILE_NOT_FOUND}
            else 409
            if exc.code == ErrorCode.PROFILE_CONFLICT
            else 422
            if exc.code == ErrorCode.PROFILE_VALIDATION_FAILED
            else 500
        )
        return JSONResponse(status_code=status, content=body)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "phase": 3}

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
                    "provider": settings.for_role(role).provider,
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

    @app.get("/api/profile")
    def get_profile():
        return {"profile": profiles.get()}

    @app.post("/api/profile/raw")
    def save_raw_profile(body: RawProfileRequest):
        if not body.raw_input.strip():
            raise CopilotError(ErrorCode.PROFILE_VALIDATION_FAILED)
        return {"profile": profiles.save_raw(body.raw_input, body.expected_version)}

    @app.post("/api/profile/normalize")
    def normalize_profile(body: VersionRequest):
        trace = RunTrace(db, settings, "profile_workflow", "profile-v1")
        trace.execute(
            build_profile_graph(trace, profiles, router),
            {"expected_version": body.expected_version},
        )
        return {"run_id": trace.run_id, "profile": profiles.get()}

    @app.put("/api/profile")
    def edit_profile(body: EditProfileRequest):
        return {"profile": profiles.edit(body.profile, body.expected_version)}

    @app.post("/api/profile/confirm")
    def confirm_profile(body: VersionRequest):
        return {"profile": profiles.confirm(body.expected_version)}

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": jobs.list()}

    @app.post("/api/jobs/analyze")
    def analyze_job(body: AnalyzeJobRequest):
        if not body.raw_jd.strip():
            raise HTTPException(status_code=422, detail="JD cannot be blank")
        trace = RunTrace(db, settings, "job_analysis_workflow", "job-analysis-v2")
        state = trace.execute(
            build_job_graph(trace, profiles, jobs, router), {"raw_jd": body.raw_jd}
        )
        return {"job": jobs.get(state["job_id"]), "run_id": trace.run_id}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"job": job}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        if not jobs.delete(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        return {"deleted": True}

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
        # Forced replay: independent execution without business or trace writes.
        tasks = {
            "jd-requirements-v2": ("generation_model", JobRequirements),
            "scoreability-v1": ("judge_model", ScoreabilityResult),
            "jd-extract-v1": ("judge_model", JobExtraction),
            "matching-v1": ("generation_model", Matching),
            "diagnostic": ("judge_model", DiagnosticResult),
            "profile-facts-v1": ("generation_model", ProfileFacts),
            "profile-normalize-v1": ("generation_model", NormalizedProfile),
        }
        if body.task not in tasks:
            raise HTTPException(status_code=422, detail="Unsupported task")
        replay_router = ModelRouter(settings, replay_only=True)
        role, schema = tasks[body.task]
        return getattr(replay_router, role).invoke(body.task, body.payload, schema).model_dump()

    return app
