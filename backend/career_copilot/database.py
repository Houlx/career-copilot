import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import CopilotError, ErrorCode

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms REAL,
    status TEXT NOT NULL,
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS node_traces (
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
    sequence INTEGER NOT NULL,
    node_name TEXT NOT NULL,
    status TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);
CREATE INDEX IF NOT EXISTS runs_started ON workflow_runs(started_at DESC);
CREATE TABLE IF NOT EXISTS career_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    raw_input TEXT NOT NULL,
    raw_history TEXT NOT NULL,
    extracted_facts TEXT,
    normalized_raw_input TEXT,
    ai_normalized_profile TEXT,
    user_edited_profile TEXT,
    confirmed INTEGER NOT NULL DEFAULT 0,
    profile_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confirmed_at TEXT,
    last_confirmed_profile TEXT
);
CREATE TABLE IF NOT EXISTS career_evidence (
    id TEXT PRIMARY KEY,
    profile_version INTEGER NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    raw_content TEXT NOT NULL,
    normalized_content TEXT NOT NULL,
    active INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, raw_jd TEXT NOT NULL, title TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('draft','analyzing','completed','failed')),
 profile_version INTEGER NOT NULL, coverage REAL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS job_requirements (
 id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 text TEXT NOT NULL, importance TEXT NOT NULL, category TEXT NOT NULL,
 scoreability TEXT NOT NULL, sequence INTEGER NOT NULL,
 PRIMARY KEY(job_id,id), UNIQUE(job_id,sequence)
);
CREATE TABLE IF NOT EXISTS job_matches (
 job_id TEXT NOT NULL, requirement_id TEXT NOT NULL, status TEXT NOT NULL,
 reason TEXT NOT NULL, evidence_ids TEXT NOT NULL,
 PRIMARY KEY(job_id,requirement_id),
 FOREIGN KEY(job_id,requirement_id) REFERENCES job_requirements(job_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS resumes (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
 profile_version INTEGER NOT NULL, language TEXT NOT NULL CHECK(language IN ('zh','en','ja')),
 status TEXT NOT NULL CHECK(status='completed'), structured_resume_json TEXT NOT NULL,
 markdown TEXT NOT NULL, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS resumes_job ON resumes(job_id,created_at DESC);
CREATE TABLE IF NOT EXISTS resume_revision_threads (
 id TEXT PRIMARY KEY,
 base_resume_id TEXT NOT NULL UNIQUE REFERENCES resumes(id),
 current_resume_id TEXT NOT NULL REFERENCES resumes(id),
 job_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
 generation INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resume_revision_messages (
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES resume_revision_threads(id),
 role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
 created_at TEXT NOT NULL, sequence INTEGER NOT NULL, UNIQUE(thread_id,sequence)
);
CREATE TABLE IF NOT EXISTS resume_revision_proposals (
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES resume_revision_threads(id),
 base_resume_id TEXT NOT NULL REFERENCES resumes(id),
 status TEXT NOT NULL CHECK(status IN ('PENDING','ACCEPTED','REJECTED','SUPERSEDED')),
 proposal_json TEXT NOT NULL, diff_json TEXT NOT NULL,
 accepted_resume_id TEXT REFERENCES resumes(id), created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS revision_pending ON resume_revision_proposals(thread_id)
 WHERE status='PENDING';
CREATE TABLE IF NOT EXISTS resume_revision_lineage (
 resume_id TEXT PRIMARY KEY REFERENCES resumes(id),
 parent_resume_id TEXT NOT NULL UNIQUE REFERENCES resumes(id),
 revision_thread_id TEXT NOT NULL REFERENCES resume_revision_threads(id),
 revision_number INTEGER NOT NULL, UNIQUE(revision_thread_id,revision_number)
);
CREATE TABLE IF NOT EXISTS profile_basic_info (
 id INTEGER PRIMARY KEY CHECK(id=1), metadata_json TEXT NOT NULL
);
PRAGMA user_version = 6;
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        try:
            with sqlite3.connect(self.path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                yield conn
        except sqlite3.Error as exc:
            raise CopilotError(ErrorCode.PERSISTENCE_FAILED) from exc

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            if conn.execute("PRAGMA user_version").fetchone()[0] > 6:
                raise CopilotError(ErrorCode.PERSISTENCE_FAILED)
            conn.executescript(SCHEMA)

    def start_run(self, run: dict):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO workflow_runs "
                "(run_id, workflow_name, workflow_version, started_at, status) "
                "VALUES (:run_id, :workflow_name, :workflow_version, :started_at, :status)",
                run,
            )

    def finish_run(
        self,
        run_id: str,
        completed_at: str,
        duration_ms: float,
        status: str,
        error_code: str | None,
    ):
        with self.connect() as conn:
            conn.execute(
                "UPDATE workflow_runs SET completed_at=?, duration_ms=?, status=?, error_code=? "
                "WHERE run_id=?",
                (completed_at, duration_ms, status, error_code, run_id),
            )

    def add_node(self, trace: dict):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO node_traces VALUES (?, ?, ?, ?, ?)",
                (
                    trace["run_id"],
                    trace["sequence"],
                    trace["node_name"],
                    trace["status"],
                    json.dumps(trace, ensure_ascii=False),
                ),
            )

    def get_run(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return None
            nodes = conn.execute(
                "SELECT trace_json FROM node_traces WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall()
        return {**dict(row), "nodes": [json.loads(n["trace_json"]) for n in nodes]}

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            result = []
            for row in rows:
                nodes = conn.execute(
                    "SELECT trace_json FROM node_traces WHERE run_id=?", (row["run_id"],)
                ).fetchall()
                meta = [json.loads(n["trace_json"]).get("ai") for n in nodes]
                ai = [m for m in meta if m is not None]
                result.append(
                    {
                        **dict(row),
                        "total_tokens": sum(m["total_tokens"] for m in ai),
                        "models": sorted({m["model_name"] for m in ai}),
                        "node_count": len(nodes),
                    }
                )
        return result
