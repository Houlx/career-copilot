import json
from uuid import uuid4

from .errors import CopilotError, ErrorCode
from .job_store import JobStore
from .models.resumes import (
    ResumeDraft,
    fact_items,
    render_markdown,
    validate_grounding,
    validate_language,
)
from .profile_store import ProfileStore, encode
from .tracing import now


class ResumeStore:
    def __init__(self, db):
        self.db = db

    def _context(self, conn, job_id, language, expected_version=None):
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise CopilotError(ErrorCode.JOB_NOT_FOUND)
        job = JobStore(self.db)._read(conn, row)
        profile = ProfileStore(self.db)._read(conn)
        if expected_version is not None and (
            not profile
            or not profile["confirmed"]
            or profile["profile_version"] != expected_version
        ):
            raise CopilotError(ErrorCode.PROFILE_CONFLICT)
        if not profile or not profile["confirmed"]:
            raise CopilotError(ErrorCode.PROFILE_VALIDATION_FAILED)
        if job["status"] != "completed":
            raise CopilotError(ErrorCode.JOB_NOT_COMPLETED)
        if job["outdated"] or job["profile_version"] != profile["profile_version"]:
            raise CopilotError(ErrorCode.JOB_OUTDATED)
        evidence = [
            e
            for e in profile["evidence"]
            if e["active"] and e["profile_version"] == profile["profile_version"]
        ]
        payload = {
            "job": {k: job[k] for k in ("title", "raw_jd", "coverage")},
            "requirements": [
                {k: r[k] for k in ("id", "text", "importance", "category", "scoreability")}
                for r in job["requirements"]
            ],
            "matches": [
                {
                    "requirement_id": r["id"],
                    "status": r["match"]["status"],
                    "evidence_ids": r["match"]["evidence_ids"],
                    "explanation": r["match"]["reason"],
                }
                for r in job["requirements"]
                if r["match"]
            ],
            "evidence": [
                {k: e[k] for k in ("id", "type", "title", "normalized_content")} for e in evidence
            ],
            "requested_language": language,
        }
        return {
            "profile_version": profile["profile_version"],
            "evidence": evidence,
            "context": payload,
        }

    def context(self, job_id, language):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            return self._context(conn, job_id, language)

    def persist(self, state):
        draft = ResumeDraft.model_validate(state["draft"])
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._context(
                conn, state["job_id"], state["language"], state["profile_version"]
            )
            validate_grounding(draft, current["evidence"], state["profile_version"])
            validate_language(draft, state["language"])
            refs = {eid for i in fact_items(draft) for eid in i.evidence_ids}
            snapshot = [
                {k: e[k] for k in ("id", "type", "title", "normalized_content")}
                for e in current["evidence"]
                if e["id"] in refs
            ]
            resume_id = str(uuid4())
            conn.execute(
                "INSERT INTO resumes VALUES (?,?,?,?,'completed',?,?,?,?)",
                (
                    resume_id,
                    state["job_id"],
                    state["profile_version"],
                    state["language"],
                    encode(draft.model_dump()),
                    render_markdown(draft),
                    encode(snapshot),
                    now(),
                ),
            )
        return resume_id

    def _read(self, conn, row):
        result = dict(row)
        result["structured_resume"] = json.loads(result.pop("structured_resume_json"))
        result["evidence"] = json.loads(result.pop("evidence_json"))
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (result["job_id"],)).fetchone()
        result["outdated"] = (
            not job
            or JobStore(self.db)._read(conn, job)["outdated"]
            or job["profile_version"] != result["profile_version"]
            or job["status"] != "completed"
        )
        return result

    def get(self, resume_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM resumes WHERE id=?", (resume_id,)).fetchone()
            return self._read(conn, row) if row else None

    def list(self, job_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            if not conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone():
                raise CopilotError(ErrorCode.JOB_NOT_FOUND)
            return [
                self._read(conn, r)
                for r in conn.execute(
                    "SELECT * FROM resumes WHERE job_id=? ORDER BY created_at DESC", (job_id,)
                )
            ]

    def delete(self, resume_id):
        with self.db.connect() as conn:
            return conn.execute("DELETE FROM resumes WHERE id=?", (resume_id,)).rowcount > 0
