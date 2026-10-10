"""Revision proposals are product data; only accept can insert a resume."""

import json
from uuid import uuid4

from .errors import CopilotError, ErrorCode
from .models.resumes import (
    ResumeDraft,
    build_document,
    fact_items,
    render_markdown,
    semantic_content,
    validate_grounding,
    validate_language,
)
from .models.revisions import ResumeRevisionProposal, RevisionThread, revision_diff
from .profile_store import encode
from .resume_store import ResumeStore
from .tracing import now


class RevisionStore:
    def __init__(self, db):
        self.db = db
        self.resumes = ResumeStore(db)

    def _thread(self, conn, thread_id):
        row = conn.execute(
            "SELECT * FROM resume_revision_threads WHERE id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise CopilotError(ErrorCode.REVISION_THREAD_NOT_FOUND)
        return RevisionThread.model_validate(dict(row)).model_dump()

    def _gate(self, conn, thread, expected):
        if (
            thread["current_resume_id"] != expected["expected_resume_id"]
            or thread["profile_version"] != expected["expected_profile_version"]
        ):
            raise CopilotError(ErrorCode.REVISION_BASE_OUTDATED)
        row = conn.execute(
            "SELECT * FROM resumes WHERE id=?", (thread["current_resume_id"],)
        ).fetchone()
        if (
            not row
            or row["profile_version"] != thread["profile_version"]
            or row["job_id"] != thread["job_id"]
        ):
            raise CopilotError(ErrorCode.REVISION_BASE_OUTDATED)
        # A parent may only have one accepted child, even across threads/tabs.
        if conn.execute(
            "SELECT 1 FROM resume_revision_lineage WHERE parent_resume_id=?", (row["id"],)
        ).fetchone():
            raise CopilotError(ErrorCode.REVISION_BASE_OUTDATED)
        try:
            context = self.resumes._context(
                conn, thread["job_id"], row["language"], thread["profile_version"]
            )
        except CopilotError as exc:
            raise CopilotError(ErrorCode.REVISION_BASE_OUTDATED) from exc
        return dict(row), context

    def create(self, resume_id, expected):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if resume_id != expected["expected_resume_id"]:
                raise CopilotError(ErrorCode.REVISION_BASE_OUTDATED)
            existing = self._find(conn, resume_id)
            if existing:
                self._gate(conn, existing, expected)
                return existing["id"]
            row = conn.execute("SELECT * FROM resumes WHERE id=?", (resume_id,)).fetchone()
            if not row:
                raise CopilotError(ErrorCode.RESUME_NOT_FOUND)
            thread = {
                "id": str(uuid4()),
                "base_resume_id": resume_id,
                "current_resume_id": resume_id,
                "job_id": row["job_id"],
                "profile_version": row["profile_version"],
                "generation": 0,
                "created_at": now(),
                "updated_at": now(),
            }
            self._gate(conn, thread, expected)
            conn.execute(
                (
                    "INSERT INTO resume_revision_threads VALUES "
                    "(:id,:base_resume_id,:current_resume_id,:job_id,:profile_version,:generation,:created_at,:updated_at)"
                ),
                thread,
            )
            return thread["id"]

    def _find(self, conn, resume_id):
        row = conn.execute(
            "SELECT t.* FROM resume_revision_threads t WHERE t.base_resume_id=? OR t.id IN "
            "(SELECT revision_thread_id FROM resume_revision_lineage WHERE resume_id=?)",
            (resume_id, resume_id),
        ).fetchone()
        return dict(row) if row else None

    def get_for_resume(self, resume_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            if not conn.execute("SELECT 1 FROM resumes WHERE id=?", (resume_id,)).fetchone():
                raise CopilotError(ErrorCode.RESUME_NOT_FOUND)
            thread = self._find(conn, resume_id)
            return self._read(conn, thread["id"]) if thread else None

    def _read(self, conn, thread_id):
        thread = self._thread(conn, thread_id)
        messages = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM resume_revision_messages WHERE thread_id=? ORDER BY sequence",
                (thread_id,),
            )
        ]
        proposals = []
        for row in conn.execute(
            "SELECT * FROM resume_revision_proposals WHERE thread_id=? ORDER BY created_at",
            (thread_id,),
        ):
            value = dict(row)
            value["proposal"] = json.loads(value.pop("proposal_json"))
            value["diff"] = json.loads(value.pop("diff_json"))
            proposals.append(value)
        history = [
            dict(r)
            for r in conn.execute(
                (
                    "SELECT * FROM resume_revision_lineage WHERE revision_thread_id=? "
                    "ORDER BY revision_number"
                ),
                (thread_id,),
            )
        ]
        return {**thread, "messages": messages, "proposals": proposals, "history": history}

    def get(self, thread_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            return self._read(conn, thread_id)

    def _message(self, conn, thread_id, role, content):
        seq = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM resume_revision_messages WHERE thread_id=?",
            (thread_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO resume_revision_messages VALUES (?,?,?,?,?,?)",
            (str(uuid4()), thread_id, role, content, now(), seq),
        )

    def begin(self, thread_id, instruction):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            thread = self._thread(conn, thread_id)
            row, context = self._gate(conn, thread, instruction)
            conn.execute(
                (
                    "UPDATE resume_revision_proposals SET status='SUPERSEDED' WHERE "
                    "thread_id=? AND status='PENDING'"
                ),
                (thread_id,),
            )
            self._message(conn, thread_id, "user", instruction["content"])
            generation = thread["generation"] + 1
            conn.execute(
                "UPDATE resume_revision_threads SET generation=?,updated_at=? WHERE id=?",
                (generation, now(), thread_id),
            )
            recent = list(
                conn.execute(
                    (
                        "SELECT role,content FROM resume_revision_messages WHERE "
                        "thread_id=? ORDER BY sequence DESC LIMIT 10"
                    ),
                    (thread_id,),
                )
            )
            payload = {
                **context["context"],
                "current_resume": semantic_content(json.loads(row["structured_resume_json"])),
                "conversation_messages": [dict(r) for r in reversed(recent)],
                "user_instruction": instruction["content"],
            }
            return {
                "header": json.loads(row["structured_resume_json"]).get("header", {}),
                "thread_id": thread_id,
                "base_resume_id": row["id"],
                "profile_version": thread["profile_version"],
                "generation": generation,
                "context": payload,
                "evidence": context["evidence"],
                "agent_step_count": 0,
            }

    def finish(self, state):
        proposal = ResumeRevisionProposal.model_validate(state["proposal"])
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            thread = self._thread(conn, state["thread_id"])
            if thread["generation"] != state["generation"]:
                raise CopilotError(ErrorCode.REVISION_PROPOSAL_CONFLICT)
            row, context = self._gate(
                conn,
                thread,
                {
                    "expected_resume_id": state["base_resume_id"],
                    "expected_profile_version": state["profile_version"],
                },
            )
            proposal_id = None
            if proposal.updated_resume:
                self._validate(proposal.updated_resume, row, context)
                diff = revision_diff(
                    semantic_content(json.loads(row["structured_resume_json"])),
                    proposal.updated_resume.model_dump(),
                )
                if not diff:
                    raise CopilotError(ErrorCode.REVISION_VALIDATION_FAILED)
                proposal_id = str(uuid4())
                conn.execute(
                    "INSERT INTO resume_revision_proposals VALUES (?,?,?,'PENDING',?,?,NULL,?)",
                    (
                        proposal_id,
                        thread["id"],
                        row["id"],
                        encode(proposal.model_dump()),
                        encode(diff),
                        now(),
                    ),
                )
            self._message(conn, thread["id"], "assistant", proposal.assistant_message)
            conn.execute(
                "UPDATE resume_revision_threads SET updated_at=? WHERE id=?", (now(), thread["id"])
            )
        return proposal_id

    def _validate(self, draft, row, context):
        try:
            validate_grounding(draft, context["evidence"], row["profile_version"])
            validate_language(draft, row["language"])
        except CopilotError as exc:
            raise CopilotError(ErrorCode.REVISION_VALIDATION_FAILED) from exc

    def resolve(self, proposal_id, expected, accept):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            p = conn.execute(
                "SELECT * FROM resume_revision_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            if not p:
                raise CopilotError(ErrorCode.REVISION_PROPOSAL_NOT_FOUND)
            thread = self._thread(conn, p["thread_id"])
            if (
                p["status"] != "PENDING"
                or p["base_resume_id"] != expected["expected_resume_id"]
                or thread["current_resume_id"] != expected["expected_resume_id"]
                or thread["profile_version"] != expected["expected_profile_version"]
            ):
                raise CopilotError(ErrorCode.REVISION_PROPOSAL_CONFLICT)
            new_id = None
            if accept:
                row, context = self._gate(conn, thread, expected)
                proposal = ResumeRevisionProposal.model_validate_json(p["proposal_json"])
                draft = ResumeDraft.model_validate(proposal.updated_resume.model_dump())
                try:
                    self._validate(draft, row, context)
                except CopilotError as exc:
                    raise CopilotError(ErrorCode.REVISION_PROPOSAL_CONFLICT) from exc
                refs = {eid for item in fact_items(draft) for eid in item.evidence_ids}
                snapshot = [
                    {k: e[k] for k in ("id", "type", "title", "normalized_content")}
                    for e in context["evidence"]
                    if e["id"] in refs
                ]
                document = build_document(
                    draft, json.loads(row["structured_resume_json"]).get("header", {})
                )
                new_id = str(uuid4())
                conn.execute(
                    "INSERT INTO resumes VALUES (?,?,?,?,'completed',?,?,?,?)",
                    (
                        new_id,
                        row["job_id"],
                        row["profile_version"],
                        row["language"],
                        encode(document.model_dump()),
                        render_markdown(document),
                        encode(snapshot),
                        now(),
                    ),
                )
                number = conn.execute(
                    (
                        "SELECT COALESCE(MAX(revision_number),0)+1 FROM "
                        "resume_revision_lineage WHERE revision_thread_id=?"
                    ),
                    (thread["id"],),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO resume_revision_lineage VALUES (?,?,?,?)",
                    (new_id, row["id"], thread["id"], number),
                )
                conn.execute(
                    (
                        "UPDATE resume_revision_threads SET "
                        "current_resume_id=?,generation=generation+1,updated_at=? WHERE id=?"
                    ),
                    (new_id, now(), thread["id"]),
                )
            else:
                conn.execute(
                    (
                        "UPDATE resume_revision_threads SET "
                        "generation=generation+1,updated_at=? WHERE id=?"
                    ),
                    (now(), thread["id"]),
                )
            conn.execute(
                "UPDATE resume_revision_proposals SET status=?,accepted_resume_id=? WHERE id=?",
                ("ACCEPTED" if accept else "REJECTED", new_id, proposal_id),
            )
            return {"resume_id": new_id, "thread_id": thread["id"]}
