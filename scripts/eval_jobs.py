"""Synthetic reviewed evaluation. Offline by default; explicit live gate, no retries."""

import argparse
import json
from pathlib import Path

from career_copilot.business_eval import announce, evaluation_model, failure_report
from career_copilot.config import Settings
from career_copilot.errors import CopilotError
from career_copilot.job_eval import extraction_metrics, matching_metrics, scoreability_metrics
from career_copilot.models.jobs import (
    JobRequirements,
    Matching,
    ScoreabilityResult,
    apply_classifications,
    guard_requirements,
)
from career_copilot.models.router import AI_OBSERVER
from career_copilot.tracing import redact
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def requirement_input(requirements):
    return [{k: r[k] for k in ("text", "importance", "category")} for r in requirements]


def accuracy(metrics):
    correct = sum(
        metrics[f"{label}_correct"] for label in ("SCORED", "NEEDS_CONFIRMATION", "NON_SCORABLE")
    )
    total = sum(
        metrics[f"{label}_total"] for label in ("SCORED", "NEEDS_CONFIRMATION", "NON_SCORABLE")
    )
    return {
        "correct": correct,
        "total": total,
        "overall_accuracy": correct / total if total else None,
        **{
            f"{label}_accuracy": metrics[f"{label}_correct"] / metrics[f"{label}_total"]
            if metrics[f"{label}_total"]
            else None
            for label in ("SCORED", "NEEDS_CONFIRMATION", "NON_SCORABLE")
        },
    }


def run_suite(settings, suite, cases, *, live=False, predictions=None):
    task, schema = {
        "jd_extraction": ("jd-requirements-v2", JobRequirements),
        "scoreability": ("scoreability-v1", ScoreabilityResult),
        "matching": ("matching-v1", Matching),
    }[suite]
    model = evaluation_model(settings, task, live=live)
    announce(model, suite, len(cases), live=live)
    totals, raw_totals, outputs, metadata = {}, {}, {}, []
    # All reviewed scoreability requirements fit in one native questions request.
    batch = None
    if suite == "scoreability" and not predictions:
        inputs = [r for case in cases for r in requirement_input(case["expected"]["requirements"])]
        result = model.invoke(task, {"requirements": inputs}, schema, max_attempts=1)
        batch = apply_classifications(inputs, result.output)
        metadata.append(result.metadata)
    offset = 0
    for case in cases:
        expected = case["expected"]
        if suite == "scoreability":
            n = len(expected["requirements"])
            raw = (
                predictions[suite][case["id"]]["requirements"]
                if predictions
                else batch[offset : offset + n]
            )
            offset += n
            # Validate legacy predictions against the unchanged report contract.
            from career_copilot.models.jobs import Requirement

            raw = [Requirement.model_validate(r).model_dump() for r in raw]
            guarded, adjusted = guard_requirements(raw)
            actual = {"requirements": guarded}
            metrics = scoreability_metrics(expected["requirements"], guarded)
            raw_metrics = scoreability_metrics(expected["requirements"], raw)
            for key, value in raw_metrics.items():
                raw_totals[key] = raw_totals.get(key, 0) + value
            outputs[case["id"]] = {"raw": raw, "guarded": guarded, "guard_adjusted": adjusted}
        else:
            payload = case.get("payload") or {"raw_jd": case["raw_jd"]}
            if predictions:
                value = predictions[suite][case["id"]]
                if suite == "jd_extraction":
                    value = {**value, "requirements": requirement_input(value["requirements"])}
                actual = schema.model_validate(value).model_dump()
            else:
                result = model.invoke(task, payload, schema, max_attempts=1)
                actual = result.output.model_dump()
                metadata.append(result.metadata)
            if suite == "matching":
                metrics = matching_metrics(expected, actual, payload["evidence"])
            else:
                reference = {
                    **expected,
                    "requirements": requirement_input(expected["requirements"]),
                }
                metrics = extraction_metrics(reference, actual)
            outputs[case["id"]] = actual
        for key, value in metrics.items():
            totals[key] = totals.get(key, 0) + value
    rates = {}
    for correct, total, label in (
        ("importance_correct", "importance_total", "importance_accuracy"),
        ("category_correct", "category_total", "category_accuracy"),
        ("status_agreement", "status_total", "status_agreement_rate"),
        ("valid_citations", "citation_total", "valid_citation_rate"),
        ("unsupported_positive", "positive_total", "unsupported_positive_match_rate"),
    ):
        if total in totals:
            rates[label] = totals[correct] / totals[total] if totals[total] else None
    report = {
        "cases": len(cases),
        "task": task,
        "metrics": totals,
        "rates": rates,
        "outputs": outputs,
        "calls": metadata,
        "request_count": len(metadata) if live else 0,
        "request_success": True,
        "schema_mapping_success": True,
        "latency_ms": sum(m["latency_ms"] for m in metadata),
        **{
            key: sum(m[key] for m in metadata)
            for key in ("input_tokens", "output_tokens", "total_tokens", "retry_count")
        },
    }
    if suite == "scoreability":
        report.update(raw=accuracy(raw_totals), guarded=accuracy(totals))
        report["rates"].update(accuracy(totals))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--suite", choices=("jd_extraction", "scoreability", "matching"))
    parser.add_argument("--case")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.case and not args.suite:
        parser.error("--case requires --suite")
    if args.live and (args.predictions or args.suite not in ("jd_extraction", "scoreability")):
        parser.error("Live supports only scoreability or a single JD extraction case")
    if args.live and args.suite == "jd_extraction" and not args.case:
        parser.error("Live JD extraction requires one --case")
    try:
        settings = (
            Settings()
            if args.live
            else Settings(
                _env_file=None, ai_mode="replay", replay_dir=ROOT / "backend/fixtures/replay"
            )
        )
        if args.live and settings.ai_mode != "live":
            parser.error("Requires both --live and AI_MODE=live")
    except ValidationError:
        parser.error("Invalid provider configuration; check model, URL and key")
    predictions = json.loads(args.predictions.read_text()) if args.predictions else None
    report = {}
    observed = []
    token = AI_OBSERVER.set(observed.append)
    try:
        for suite in [args.suite] if args.suite else ["jd_extraction", "scoreability", "matching"]:
            cases = json.loads((ROOT / f"evals/{suite}/cases.json").read_text())
            if args.case:
                cases = [c for c in cases if c["id"] == args.case]
                if not cases:
                    parser.error("Unknown synthetic case ID")
            report[suite] = run_suite(
                settings, suite, cases, live=args.live, predictions=predictions
            )
    except CopilotError as exc:
        result = redact(failure_report(exc, observed, live=args.live), settings.secrets())
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
        raise SystemExit(1) from None
    finally:
        AI_OBSERVER.reset(token)
    result = redact(
        {
            "reviewed_fixture_metrics": report,
            "live_api_calls": sum(s["request_count"] for s in report.values()),
        },
        settings.secrets(),
    )
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
