"""Offline reviewed synthetic revision regression. Never issues live requests."""

import argparse
import copy
import json
from pathlib import Path

from career_copilot.config import Settings
from career_copilot.errors import CopilotError
from career_copilot.models.resumes import grounding_metrics, validate_grounding, validate_language
from career_copilot.models.revisions import ResumeRevisionProposal, revision_diff
from career_copilot.models.router import ModelRouter

ROOT = Path(__file__).resolve().parents[1]


def evaluate(case, model):
    payload = copy.deepcopy(case["payload"])
    base = copy.deepcopy(payload["current_resume"])
    output = model.invoke("resume-revision-agent-v1", payload, ResumeRevisionProposal).output
    draft = output.updated_resume
    metrics = {
        "schema_success": True,
        "expected_action": output.action == case["expected_action"],
        "valid_evidence_refs": True,
        "numeric_grounding": True,
        "unsupported_fact_count": 0,
        "language_preserved": True,
        "base_resume_unchanged_if_not_accepted": base == payload["current_resume"],
    }
    error = None
    if draft:
        evidence = [{**e, "active": 1, "profile_version": 3} for e in payload["evidence"]]
        grounding = grounding_metrics(draft, evidence, 3)
        metrics.update(
            valid_evidence_refs=grounding["invalid_evidence_reference_count"] == 0,
            numeric_grounding=grounding["numeric_grounding_failure_count"] == 0,
            unsupported_fact_count=grounding["unsupported_fact_item_count"],
            language_preserved=draft.language == base["language"],
        )
        try:
            validate_grounding(draft, evidence, 3)
            validate_language(draft, base["language"])
            assert revision_diff(base, draft.model_dump())
        except CopilotError as exc:
            error = exc.code.value
    passed = (
        error == case["expected_error"]
        and metrics["expected_action"]
        and metrics["base_resume_unchanged_if_not_accepted"]
    )
    if not case["expected_error"]:
        passed = passed and all(
            metrics[k] for k in ("valid_evidence_refs", "numeric_grounding", "language_preserved")
        )
    return {"case_id": case["id"], "passed": passed, "metrics": metrics, "validation_error": error}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings(
        _env_file=None, ai_mode="replay", replay_dir=ROOT / "backend/fixtures/replay"
    )
    model = ModelRouter(settings, replay_only=True).generation_model
    cases = json.loads((ROOT / "evals/resume_revision/cases.json").read_text())
    results = [evaluate(case, model) for case in cases]
    report = {
        "success": all(r["passed"] for r in results),
        "results": results,
        "live_api_calls": 0,
        "limits": (
            "Reviewed fixture regression; unsupported_fact_count checks refs only, "
            "not semantic entailment. Base immutability here is an input check; "
            "API tests verify DB immutability."
        ),
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded)
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
