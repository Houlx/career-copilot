"""Offline reviewed fixture evaluation; optional predictions grouped by suite and case ID."""

import argparse
import json
from pathlib import Path

from career_copilot.config import Settings
from career_copilot.job_eval import extraction_metrics, matching_metrics, scoreability_metrics
from career_copilot.models.jobs import JobExtraction, Matching, guard_requirements
from career_copilot.models.router import ModelRouter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path)
    args = parser.parse_args()
    predictions = json.loads(args.predictions.read_text()) if args.predictions else None
    root = Path(__file__).resolve().parents[1]
    router = ModelRouter(
        Settings(_env_file=None, replay_dir=root / "backend/fixtures/replay"), replay_only=True
    )
    report = {}
    for suite in ("jd_extraction", "scoreability", "matching"):
        cases = json.loads((root / f"evals/{suite}/cases.json").read_text())
        totals = {}
        for case in cases:
            payload = case.get("payload") or {"raw_jd": case["raw_jd"]}
            schema = Matching if suite == "matching" else JobExtraction
            model = router.generation_model if suite == "matching" else router.judge_model
            task = "matching-v1" if suite == "matching" else "jd-extract-v1"
            actual = (
                schema.model_validate(predictions[suite][case["id"]]).model_dump()
                if predictions
                else model.invoke(task, payload, schema).output.model_dump()
            )
            if suite == "matching":
                metrics = matching_metrics(case["expected"], actual, payload["evidence"])
            elif suite == "scoreability":
                guarded, _ = guard_requirements(actual["requirements"])
                metrics = scoreability_metrics(case["expected"]["requirements"], guarded)
            else:
                metrics = extraction_metrics(case["expected"], actual)
            for key, value in metrics.items():
                totals[key] = totals.get(key, 0) + value
        rates = {}
        for correct, total, label in (
            ("importance_correct", "importance_total", "importance_accuracy"),
            ("category_correct", "category_total", "category_accuracy"),
            ("SCORED_correct", "SCORED_total", "SCORED_accuracy"),
            (
                "NEEDS_CONFIRMATION_correct",
                "NEEDS_CONFIRMATION_total",
                "NEEDS_CONFIRMATION_accuracy",
            ),
            ("NON_SCORABLE_correct", "NON_SCORABLE_total", "NON_SCORABLE_accuracy"),
            ("status_agreement", "status_total", "status_agreement_rate"),
            ("valid_citations", "citation_total", "valid_citation_rate"),
            ("unsupported_positive", "positive_total", "unsupported_positive_match_rate"),
        ):
            if total in totals:
                rates[label] = totals[correct] / totals[total] if totals[total] else None
        report[suite] = {"cases": len(cases), "metrics": totals, "rates": rates}
    print(json.dumps({"reviewed_fixture_metrics": report, "live_api_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
