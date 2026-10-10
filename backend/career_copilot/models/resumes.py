"""Evidence references and numeric checks, not a semantic entailment engine."""

import re
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..errors import CopilotError, ErrorCode
from .profile import StrictModel, numbers

Language = Literal["zh", "en", "ja"]
EvidenceID = Annotated[
    str, Field(pattern=r"^career-(education|work|project|skill|other)-[0-9a-f]{64}$")
]


class FactItem(StrictModel):
    text: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    evidence_ids: list[EvidenceID] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_refs(self):
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Duplicate evidence references")
        return self


class ResumeSection(StrictModel):
    kind: Literal["skills", "experience", "projects", "education", "additional"]
    items: list[FactItem] = Field(min_length=1, max_length=200)


class ResumeDraft(StrictModel):
    language: Language
    headline: FactItem
    summary: list[FactItem] = Field(min_length=1, max_length=20)
    sections: list[ResumeSection] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def unique_sections(self):
        kinds = [s.kind for s in self.sections]
        if len(kinds) != len(set(kinds)):
            raise ValueError("Duplicate sections")
        return self


def fact_items(draft):
    return [draft.headline, *draft.summary, *(i for s in draft.sections for i in s.items)]


def grounding_metrics(draft, evidence, version):
    valid = {e["id"]: e for e in evidence if e["active"] and e["profile_version"] == version}
    items = fact_items(draft)
    invalid = unsupported = numeric = refs = 0
    for item in items:
        refs += len(item.evidence_ids)
        invalid += sum(eid not in valid for eid in item.evidence_ids)
        unsupported += int(
            not item.evidence_ids or any(eid not in valid for eid in item.evidence_ids)
        )
        source = " ".join(
            valid[eid]["normalized_content"] for eid in item.evidence_ids if eid in valid
        )
        numeric += int(not numbers(item.text) <= numbers(source))
    return {
        "resume_item_count": len(items),
        "evidence_reference_count": refs,
        "invalid_evidence_reference_count": invalid,
        "unsupported_fact_item_count": unsupported,
        "numeric_grounding_failure_count": numeric,
        "evidence_backed_item_rate": (len(items) - unsupported) / len(items),
    }


def validate_grounding(draft, evidence, version):
    metrics = grounding_metrics(draft, evidence, version)
    if any(
        metrics[k]
        for k in (
            "invalid_evidence_reference_count",
            "unsupported_fact_item_count",
            "numeric_grounding_failure_count",
        )
    ):
        raise CopilotError(ErrorCode.RESUME_GROUNDING_FAILED, diagnostics=metrics)
    return metrics


def validate_language(draft, language):
    if draft.language != language:
        raise CopilotError(ErrorCode.RESUME_VALIDATION_FAILED)
    # Lightweight deterministic contract only. Technical names may remain untranslated.
    return {"language": language, "language_matches_request": True}


LABELS = {
    "en": {
        "summary": "Summary",
        "skills": "Skills",
        "experience": "Experience",
        "projects": "Projects",
        "education": "Education",
        "additional": "Additional",
    },
    "zh": {
        "summary": "个人简介",
        "skills": "技能",
        "experience": "工作经历",
        "projects": "项目经历",
        "education": "教育经历",
        "additional": "其他",
    },
    "ja": {
        "summary": "概要",
        "skills": "スキル",
        "experience": "職務経歴",
        "projects": "プロジェクト",
        "education": "学歴",
        "additional": "その他",
    },
}


def plain(text):
    # Model text cannot introduce Markdown sections, links, HTML or hidden formatting.
    text = " ".join(text.split())
    return re.sub(r"([\\`*_{}\[\]()#+.!<>|~-])", r"\\\1", text)


def render_markdown(draft):
    labels = LABELS[draft.language]
    lines = ["# " + plain(draft.headline.text), "", "## " + labels["summary"], ""]
    lines.extend(plain(i.text) + "\n" for i in draft.summary)
    for section in draft.sections:
        lines.extend(["## " + labels[section.kind], ""])
        lines.extend("- " + plain(i.text) for i in section.items)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def fake_resume(payload):
    # Identity demo: deliberately does not simulate translation or resume quality.
    e = payload["evidence"][0]
    item = {"text": e["normalized_content"], "evidence_ids": [e["id"]]}
    return {
        "language": payload["requested_language"],
        "headline": item,
        "summary": [item],
        "sections": [{"kind": "skills", "items": [item]}, {"kind": "experience", "items": [item]}],
    }
