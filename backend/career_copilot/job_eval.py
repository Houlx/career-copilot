"""Reviewed fixture metrics; exact text comparison is deliberately conservative."""

from collections import Counter


def extraction_metrics(expected: dict, actual: dict) -> dict:
    gold, predicted = expected["requirements"], actual["requirements"]
    missing = sum(
        (Counter(r["text"] for r in gold) - Counter(r["text"] for r in predicted)).values()
    )
    added = sum((Counter(r["text"] for r in predicted) - Counter(r["text"] for r in gold)).values())
    result = {"requirement_missing": missing, "unsupported_addition": added}
    # Match duplicate texts in order rather than silently dropping duplicates.
    for field in ("importance", "category", "scoreability"):
        remaining = list(predicted)
        correct = 0
        for r in gold:
            match = next((p for p in remaining if p["text"] == r["text"]), None)
            if match is not None:
                correct += match[field] == r[field]
                remaining.remove(match)
        result[f"{field}_correct"] = correct
        result[f"{field}_total"] = len(gold)
    return result


def scoreability_metrics(expected: list[dict], actual: list[dict]) -> dict:
    result = {}
    for label in ("SCORED", "NEEDS_CONFIRMATION", "NON_SCORABLE"):
        gold = [r for r in expected if r["scoreability"] == label]
        result[f"{label}_correct"] = sum(
            any(p["text"] == r["text"] and p["scoreability"] == label for p in actual) for r in gold
        )
        result[f"{label}_total"] = len(gold)
    return result


def matching_metrics(expected: dict, actual: dict, evidence: list[dict]) -> dict:
    gold = {m["requirement_index"]: m for m in expected["matches"]}
    ids = {e["id"] for e in evidence}
    citation_total = citation_valid = positive = unsupported = 0
    agree = 0
    seen = set()
    for m in actual["matches"]:
        ref = gold.get(m["requirement_index"])
        duplicate = m["requirement_index"] in seen
        seen.add(m["requirement_index"])
        agree += bool(ref and ref["status"] == m["status"] and not duplicate)
        citation_total += len(m["evidence_ids"])
        citation_valid += sum(e in ids for e in m["evidence_ids"])
        if m["status"] != "GAP":
            positive += 1
            unsupported += bool(
                duplicate
                or not ref
                or ref["status"] == "GAP"
                or not m["evidence_ids"]
                or not set(m["evidence_ids"]) <= set(ref["evidence_ids"])
            )
    return {
        "status_agreement": agree,
        "status_total": len(gold),
        "valid_citations": citation_valid,
        "citation_total": citation_total,
        "unsupported_positive": unsupported,
        "positive_total": positive,
    }
