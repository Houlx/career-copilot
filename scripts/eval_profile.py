"""Offline replay evaluation. Optional --predictions contains case-id -> facts outputs."""

import argparse
import json
from pathlib import Path

from career_copilot.config import Settings
from career_copilot.models.profile import ProfileFacts
from career_copilot.models.router import ModelRouter
from career_copilot.profile_eval import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "evals/profile_extraction/cases.json").read_text())
    predictions = json.loads(args.predictions.read_text()) if args.predictions else None
    model = ModelRouter(
        Settings(_env_file=None, ai_mode="replay", replay_dir=root / "backend/fixtures/replay")
    ).judge_model
    totals: dict[str, int] = {}
    for case in cases:
        actual = (
            ProfileFacts.model_validate(predictions[case["id"]])
            if predictions
            else (
                model.invoke(
                    "profile-facts-v1", {"raw_input": case["raw_input"]}, ProfileFacts
                ).output
            )
        )
        result = evaluate(ProfileFacts.model_validate(case["expected"]), actual)
        for key, value in result.items():
            totals[key] = totals.get(key, 0) + value
    print(json.dumps({"cases": len(cases), "metrics": totals, "live_api_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
