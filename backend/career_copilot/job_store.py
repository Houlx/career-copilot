import json
from uuid import uuid4

from .database import Database
from .errors import CopilotError, ErrorCode
from .models.jobs import Matching, coverage, validate_matches
from .profile_store import ProfileStore, encode
from .tracing import now


class JobStore:
    def __init__(self, db: Database):
        self.db = db

    def persist(self, state):
        requirements = state["requirements"]
        matches = Matching.model_validate(state["matches"])
        version = state["profile_version"]
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profile = ProfileStore(self.db)._read(conn)
            if not profile or not profile["confirmed"] or profile["profile_version"] != version:
                raise CopilotError(ErrorCode.PROFILE_CONFLICT)
            validate_matches(requirements, matches, profile["evidence"], version)
            job_id, stamp = str(uuid4()), now()
            conn.execute(
                "INSERT INTO jobs VALUES (?,?,?,'completed',?,?,?,?,?)",
                (
                    job_id,
                    state["raw_jd"],
                    state["extraction"]["title"],
                    version,
                    coverage(requirements, matches),
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            for i, r in enumerate(requirements):
                conn.execute(
                    "INSERT INTO job_requirements VALUES (?,?,?,?,?,?,?)",
                    (
                        f"req-{i + 1:03}",
                        job_id,
                        r["text"],
                        r["importance"],
                        r["category"],
                        r["scoreability"],
                        i,
                    ),
                )
            for m in matches.matches:
                conn.execute(
                    "INSERT INTO job_matches VALUES (?,?,?,?,?)",
                    (
                        job_id,
                        f"req-{m.requirement_index + 1:03}",
                        m.status,
                        m.reason,
                        encode(m.evidence_ids),
                    ),
                )
        return job_id

    def _read(self, conn, row):
        job = dict(row)
        profile = conn.execute(
            "SELECT confirmed,profile_version FROM career_profile WHERE id=1"
        ).fetchone()
        job["outdated"] = (
            not profile
            or not profile["confirmed"]
            or profile["profile_version"] != job["profile_version"]
        )
        requirements = []
        for row in conn.execute(
            "SELECT * FROM job_requirements WHERE job_id=? ORDER BY sequence", (job["id"],)
        ):
            r = dict(row)
            match = conn.execute(
                "SELECT * FROM job_matches WHERE job_id=? AND requirement_id=?",
                (job["id"], r["id"]),
            ).fetchone()
            r["match"] = dict(match) if match else None
            if match:
                ids = json.loads(match["evidence_ids"])
                r["match"]["evidence_ids"] = ids
                # Historical inactive evidence remains available for old report citations.
                r["match"]["evidence"] = [
                    dict(e)
                    for eid in ids
                    for e in conn.execute(
                        "SELECT id,type,title,normalized_content FROM career_evidence WHERE id=?",
                        (eid,),
                    )
                ]
            requirements.append(r)
        job["requirements"] = requirements
        return job

    def get(self, job_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._read(conn, row) if row else None

    def list(self):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            return [
                self._read(conn, r)
                for r in conn.execute("SELECT * FROM jobs ORDER BY created_at DESC")
            ]

    def delete(self, job_id):
        with self.db.connect() as conn:
            return conn.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount > 0
