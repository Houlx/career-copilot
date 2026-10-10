"""Explicit, opt-in live provider diagnostic; never used by regular tests."""

import argparse
import json

from .config import Settings
from .models.prompts import DiagnosticResult
from .models.router import ModelRouter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Authorize a real billable request")
    args = parser.parse_args()
    settings = Settings()
    if not args.live or settings.ai_mode != "live":
        parser.error("Requires both --live and AI_MODE=live")
    result = ModelRouter(settings).judge_model.invoke("diagnostic", {}, DiagnosticResult)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
