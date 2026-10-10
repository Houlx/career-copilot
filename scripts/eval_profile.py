"""Synthetic Profile facts evaluation; offline default, single-case gated live."""

import argparse
import json
from pathlib import Path

from career_copilot.business_eval import announce, evaluation_model, failure_report
from career_copilot.config import Settings
from career_copilot.errors import CopilotError
from career_copilot.models.diagnostics import DIAGNOSTICS_ENABLED
from career_copilot.models.profile import ProfileFacts, validate_facts
from career_copilot.models.router import AI_OBSERVER
from career_copilot.profile_eval import evaluate
from career_copilot.tracing import redact
from pydantic import ValidationError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--case")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.live and (not args.case or args.predictions):
        parser.error("Live requires one synthetic --case and no predictions")
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "evals/profile_extraction/cases.json").read_text())
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            parser.error("Unknown synthetic case ID")
    try:
        settings = (
            Settings()
            if args.live
            else Settings(
                _env_file=None, ai_mode="replay", replay_dir=root / "backend/fixtures/replay"
            )
        )
        model = evaluation_model(settings, "profile-facts-v1", live=args.live)
    except (ValidationError, ValueError):
        parser.error("Requires both --live and AI_MODE=live with valid provider configuration")
    announce(model, "profile_extraction", len(cases), live=args.live)
    predictions = json.loads(args.predictions.read_text()) if args.predictions else None
    totals, calls, outputs = {}, [], {}
    observed = []
    token = AI_OBSERVER.set(observed.append)
    diagnostics_token = DIAGNOSTICS_ENABLED.set(settings.payloads_enabled)
    try:
        for case in cases:
            if predictions:
                actual = ProfileFacts.model_validate(predictions[case["id"]])
            else:
                result = model.invoke(
                    "profile-facts-v1",
                    {"raw_input": case["raw_input"]},
                    ProfileFacts,
                    max_attempts=1,
                )
                actual = result.output
                calls.append(result.metadata)
            validate_facts(actual, case["raw_input"])
            outputs[case["id"]] = actual.model_dump()
            for key, value in evaluate(
                ProfileFacts.model_validate(case["expected"]), actual
            ).items():
                totals[key] = totals.get(key, 0) + value
    except CopilotError as exc:
        result = redact(failure_report(exc, observed, live=args.live), settings.secrets())
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
        raise SystemExit(1) from None
    finally:
        AI_OBSERVER.reset(token)
        DIAGNOSTICS_ENABLED.reset(diagnostics_token)
    result = redact(
        {
            "cases": len(cases),
            "task": "profile-facts-v1",
            "metrics": totals,
            "live_api_calls": len(calls) if args.live else 0,
            "request_success": True,
            "schema_valid": True,
            "schema_mapping_success": True,
            "business_validation_success": True,
            "calls": calls,
            "outputs": outputs,
        },
        settings.secrets(),
    )
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
