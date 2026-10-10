"""Bounded revision contracts; instructions are never candidate evidence."""

from copy import deepcopy
from typing import Literal

from pydantic import Field, model_validator

from .profile import StrictModel
from .resumes import ResumeDraft

AgentAction = Literal["REVISE", "ASK_USER", "NO_CHANGE"]
ProposalStatus = Literal["PENDING", "ACCEPTED", "REJECTED", "SUPERSEDED"]


class RevisionChange(StrictModel):
    section_kind: str = Field(min_length=1, max_length=100, pattern=r"\S")
    summary: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class ResumeRevisionProposal(StrictModel):
    action: AgentAction
    assistant_message: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    updated_resume: ResumeDraft | None
    change_summary: list[RevisionChange] = Field(max_length=50)

    @model_validator(mode="after")
    def action_content(self):
        if (self.action == "REVISE") != (self.updated_resume is not None):
            raise ValueError("Only REVISE requires an updated resume")
        if self.action != "REVISE" and self.change_summary:
            raise ValueError("No changes for a response without a revision")
        return self


class RevisionExpected(StrictModel):
    expected_resume_id: str = Field(min_length=1, max_length=100)
    expected_profile_version: int = Field(ge=1)


class RevisionInstruction(RevisionExpected):
    content: str = Field(min_length=1, max_length=6000, pattern=r"\S")


class RevisionThread(StrictModel):
    id: str
    base_resume_id: str
    current_resume_id: str
    job_id: str
    profile_version: int
    generation: int
    created_at: str
    updated_at: str


class RevisionMessage(StrictModel):
    id: str
    thread_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str


class RevisionArtifact(StrictModel):
    id: str
    thread_id: str
    base_resume_id: str
    status: ProposalStatus
    proposal: ResumeRevisionProposal
    diff: list[dict]
    accepted_resume_id: str | None
    created_at: str


def revision_diff(before, after):
    """Authoritative deterministic diff, including ordering and evidence changes."""
    changes = []
    for key in ("headline", "summary", "sections"):
        if before[key] != after[key]:
            changes.append({"section_kind": key, "before": before[key], "after": after[key]})
    return changes


def fake_revision(payload):
    """Synthetic flow demo; does not simulate semantic reasoning or translation."""
    instruction = payload["user_instruction"].lower()
    response = {
        "action": "NO_CHANGE",
        "assistant_message": (
            "当前 Revision 只能使用已确认 Career Evidence；新经历请先更新 Profile 并确认。"
        ),
        "updated_resume": None,
        "change_summary": [],
    }
    if any(word in instruction for word in ("英文", "english")):
        return {**response, "assistant_message": "Revision 暂不支持切换语言，请保持当前简历语言。"}
    if any(word in instruction for word in ("cuda", "99%", "no change", "不变")):
        return response
    if any(word in instruction for word in ("更厉害", "clarify", "不明确")):
        return {
            **response,
            "action": "ASK_USER",
            "assistant_message": "希望突出技术深度、项目管理还是业务影响？",
        }
    draft = deepcopy(payload["current_resume"])
    if any(word in instruction for word in ("排序", "顺序", "reorder")):
        draft["sections"].reverse()
    elif "multi evidence" in instruction:
        draft["summary"][0] = {
            "text": " ".join(e["normalized_content"] for e in payload["evidence"]),
            "evidence_ids": [e["id"] for e in payload["evidence"]],
        }
    else:
        # Local supported rewording: punctuation only, without new candidate facts.
        item = draft["sections"][0]["items"][0]
        item["text"] = item["text"].rstrip(".。") + ";"
    if draft == payload["current_resume"]:
        return {**response, "assistant_message": "已满足该要求。"}
    return {
        "action": "REVISE",
        "assistant_message": "请查看修改建议并明确确认。Fake 模式仅验证流程。",
        "updated_resume": draft,
        "change_summary": [{"section_kind": "resume", "summary": "调整指定内容的表达或顺序。"}],
    }
