"""Reviewed synthetic grounding regression; one explicitly gated live baseline, no retries."""

import argparse
import json
from pathlib import Path

from career_copilot.business_eval import announce, evaluation_model, failure_report
from career_copilot.config import Settings
from career_copilot.errors import CopilotError
from career_copilot.models.resumes import (
    ResumeDraft,
    build_document,
    render_markdown,
    validate_grounding,
    validate_language,
)
from career_copilot.models.router import AI_OBSERVER
from career_copilot.tracing import redact

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = (
        Settings()
        if args.live
        else Settings(_env_file=None, ai_mode="replay", replay_dir=ROOT / "backend/fixtures/replay")
    )
    if args.live and settings.ai_mode != "live":
        parser.error("Requires --live and AI_MODE=live")
    if args.live and (
        settings.for_role("generation").provider != "openai_compatible"
        or settings.for_role("generation").structured_output_mode != "json_object"
    ):
        parser.error(
            "Live baseline requires validated GLM openai_compatible json_object configuration"
        )
    cases = json.loads((ROOT / "evals/resumes/cases.json").read_text())
    if args.live:
        cases = cases[:1]
    model = evaluation_model(settings, "resume-generation-v2", live=args.live)
    announce(model, "resume-generation-v2", len(cases), live=args.live)
    calls, results = [], []
    token = AI_OBSERVER.set(calls.append)
    success = True
    try:
        for case in cases:
            call_start = len(calls)
            schema_success = False
            try:
                output = model.invoke(
                    "resume-generation-v2", case["payload"], ResumeDraft, max_attempts=1
                ).output
                schema_success = True
                evidence = [
                    {**e, "active": 1, "profile_version": 3} for e in case["payload"]["evidence"]
                ]
                metrics = validate_grounding(output, evidence, 3)
                validate_language(output, case["payload"]["requested_language"])
                markdown = render_markdown(build_document(output, {"name": "Synthetic Candidate"}))
                assert markdown and "career-" not in markdown
                if case["expected_error"]:
                    success = False
                results.append(
                    {
                        "case_id": case["id"],
                        "passed": not case["expected_error"],
                        "metrics": metrics,
                        "markdown_render_success": True,
                        "schema_mapping_success": True,
                    }
                )
                if args.live:
                    # Synthetic text only: retain for human review of language, GAP and PARTIAL.
                    results[-1]["synthetic_output"] = output.model_dump()
                    results[-1]["synthetic_markdown"] = markdown
            except CopilotError as exc:
                passed = not args.live and exc.code.value == case["expected_error"]
                success = success and passed
                results.append(
                    {
                        "case_id": case["id"],
                        "passed": passed,
                        **failure_report(exc, list(calls[call_start:]), live=args.live),
                        "schema_mapping_success": schema_success,
                    }
                )
                if args.live:
                    break
    finally:
        AI_OBSERVER.reset(token)
    report = redact(
        {
            "success": success,
            "results": results,
            "calls": calls,
            "live_api_calls": len(calls) if args.live else 0,
            "retry_count": sum(c["retry_count"] for c in calls),
        },
        settings.secrets(),
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
