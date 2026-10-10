"""Deterministic exact fact comparison for reviewed synthetic extraction cases."""

from collections import Counter

from .models.profile import ProfileFacts, numbers


def evaluate(expected: ProfileFacts, actual: ProfileFacts) -> dict:
    def signatures(result):
        return Counter((f.type, f.title, f.content, f.source_quote) for f in result.facts)

    gold, predicted = signatures(expected), signatures(actual)
    expected_sources = {(f.title, f.source_quote): f for f in expected.facts}
    actual_sources = {(f.title, f.source_quote): f for f in actual.facts}
    classified = sum(
        key in actual_sources and actual_sources[key].type == fact.type
        for key, fact in expected_sources.items()
    )
    numeric_total = numeric_preserved = 0
    for key, fact in expected_sources.items():
        values = numbers(fact.content)
        numeric_total += len(values)
        numeric_preserved += (
            len(values & numbers(actual_sources[key].content)) if (key in actual_sources) else 0
        )
    return {
        "fact_omission": sum((gold - predicted).values()),
        "unsupported_fact_addition": sum((predicted - gold).values()),
        "type_correct": classified,
        "type_total": len(expected_sources),
        "numeric_preserved": numeric_preserved,
        "numeric_total": numeric_total,
    }
