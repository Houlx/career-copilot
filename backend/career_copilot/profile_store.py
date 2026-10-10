"""Single current profile; all read/modify/write operations use one SQLite transaction."""

import hashlib
import json

from .database import Database
from .errors import CopilotError, ErrorCode
from .models.profile import NormalizedProfile, ProfileFacts, validate_profile
from .tracing import now

JSON_FIELDS = (
    "raw_history",
    "extracted_facts",
    "ai_normalized_profile",
    "user_edited_profile",
    "last_confirmed_profile",
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ProfileStore:
    def __init__(self, db: Database):
        self.db = db

    def _read(self, conn):
        row = conn.execute("SELECT * FROM career_profile WHERE id=1").fetchone()
        if row is None:
            return None
        profile = dict(row)
        for key in JSON_FIELDS:
            profile[key] = json.loads(profile[key]) if profile[key] is not None else None
        profile["confirmed"] = bool(profile["confirmed"])
        profile["effective_profile"] = (
            profile["user_edited_profile"] or profile["ai_normalized_profile"]
        )
        profile["evidence"] = [
            dict(row)
            for row in conn.execute("SELECT * FROM career_evidence WHERE active=1 ORDER BY id")
        ]
        profile["state"] = (
            "confirmed"
            if profile["confirmed"]
            else "raw_saved"
            if profile["normalized_raw_input"] != profile["raw_input"]
            else "edited"
            if profile["user_edited_profile"]
            else "draft_ready"
        )
        return profile

    def get(self):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            return self._read(conn)

    def _locked(self, conn, version: int):
        conn.execute("BEGIN IMMEDIATE")
        profile = self._read(conn)
        if profile is None:
            raise CopilotError(ErrorCode.PROFILE_NOT_FOUND)
        if profile["profile_version"] != version:
            raise CopilotError(ErrorCode.PROFILE_CONFLICT)
        return profile

    def save_raw(self, raw: str, version: int):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profile = self._read(conn)
            if (profile["profile_version"] if profile else 0) != version:
                raise CopilotError(ErrorCode.PROFILE_CONFLICT)
            if profile and profile["raw_input"] == raw:
                return profile
            stamp = now()
            history = (profile["raw_history"] if profile else []) + [
                {"raw_input": raw, "saved_at": stamp, "profile_version": version + 1}
            ]
            if profile is None:
                conn.execute(
                    "INSERT INTO career_profile "
                    "(id,raw_input,raw_history,profile_version,created_at,updated_at) "
                    "VALUES (1,?,?,?,?,?)",
                    (raw, encode(history), 1, stamp, stamp),
                )
            else:
                # Preserve the old draft, confirmed snapshot and evidence on new raw input.
                conn.execute(
                    "UPDATE career_profile SET raw_input=?,raw_history=?,profile_version=?,"
                    "confirmed=0,confirmed_at=NULL,updated_at=? WHERE id=1",
                    (raw, encode(history), version + 1, stamp),
                )
            return self._read(conn)

    def persist_draft(
        self, version: int, raw: str, facts: ProfileFacts, normalized: NormalizedProfile
    ):
        validate_profile(normalized, facts, generated=True)
        with self.db.connect() as conn:
            profile = self._locked(conn, version)
            if profile["raw_input"] != raw:
                raise CopilotError(ErrorCode.PROFILE_CONFLICT)
            facts_json, normalized_json = facts.model_dump(), normalized.model_dump()
            if (
                profile["extracted_facts"] == facts_json
                and profile["ai_normalized_profile"] == normalized_json
                and profile["normalized_raw_input"] == raw
                and profile["user_edited_profile"] is None
            ):
                return profile
            conn.execute(
                "UPDATE career_profile SET extracted_facts=?,ai_normalized_profile=?,"
                "normalized_raw_input=?,user_edited_profile=NULL,confirmed=0,confirmed_at=NULL,"
                "profile_version=?,updated_at=? WHERE id=1",
                (encode(facts_json), encode(normalized_json), raw, version + 1, now()),
            )
            return self._read(conn)

    def _editable(self, profile):
        if (
            not profile["effective_profile"]
            or profile["normalized_raw_input"] != profile["raw_input"]
        ):
            raise CopilotError(ErrorCode.PROFILE_VALIDATION_FAILED)

    def edit(self, edited: NormalizedProfile, version: int):
        with self.db.connect() as conn:
            profile = self._locked(conn, version)
            self._editable(profile)
            validate_profile(
                edited, ProfileFacts.model_validate(profile["extracted_facts"]), generated=False
            )
            if edited.model_dump() == profile["effective_profile"]:
                return profile
            conn.execute(
                "UPDATE career_profile SET user_edited_profile=?,confirmed=0,confirmed_at=NULL,"
                "profile_version=?,updated_at=? WHERE id=1",
                (encode(edited.model_dump()), version + 1, now()),
            )
            return self._read(conn)

    def confirm(self, version: int):
        with self.db.connect() as conn:
            profile = self._locked(conn, version)
            self._editable(profile)
            if profile["confirmed"]:
                return profile
            effective = NormalizedProfile.model_validate(profile["effective_profile"])
            facts = ProfileFacts.model_validate(profile["extracted_facts"])
            validate_profile(effective, facts, generated=False)
            stamp, new_version = now(), version + 1
            conn.execute("UPDATE career_evidence SET active=0")
            for entry in effective.entries:
                fact = facts.facts[entry.source_index]
                ai_entry = next(
                    e
                    for e in profile["ai_normalized_profile"]["entries"]
                    if e["source_index"] == entry.source_index
                )
                # User additions are explicitly attributed to the user; keep original quote too.
                raw_content = encode(
                    {
                        "source_quote": fact.source_quote,
                        "extracted_content": fact.content,
                        "user_edit": entry.model_dump() if entry.model_dump() != ai_entry else None,
                    }
                )
                digest = hashlib.sha256(
                    encode({"entry": entry.model_dump(), "raw": raw_content}).encode()
                ).hexdigest()
                evidence_id = f"career-{entry.type}-{digest}"
                conn.execute(
                    "INSERT INTO career_evidence VALUES (?,?,?,?,?,?,1,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET profile_version=excluded.profile_version,"
                    "active=1,updated_at=excluded.updated_at",
                    (
                        evidence_id,
                        new_version,
                        entry.type,
                        entry.title,
                        raw_content,
                        entry.content,
                        stamp,
                        stamp,
                    ),
                )
            snapshot = {
                "profile": effective.model_dump(),
                "profile_version": new_version,
                "confirmed_at": stamp,
            }
            conn.execute(
                "UPDATE career_profile SET confirmed=1,confirmed_at=?,profile_version=?,"
                "last_confirmed_profile=?,updated_at=? WHERE id=1",
                (stamp, new_version, encode(snapshot), stamp),
            )
            return self._read(conn)

    def confirmed_profile(self):
        """Gate for future consumers: active evidence alone never grants authorization."""
        profile = self.get()
        if not profile or not profile["confirmed"]:
            raise CopilotError(ErrorCode.PROFILE_VALIDATION_FAILED)
        return profile
