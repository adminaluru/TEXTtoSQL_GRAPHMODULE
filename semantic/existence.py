"""Which concepts only qualify the result records (EXISTS conditions).

Shared by SemanticQueryNormalizer (what is executed) and AnswerService (what
the answer says), so an answer can never omit a condition that was applied.
"""


def existence_concepts(
    intent: str,
    concepts: list[str],
    result_concept: str | None,
    measure_concept: str | None,
    filter_concepts: set[str],
    grouping_concepts: set[str],
) -> list[str]:
    # LIST, and COUNT / aggregate over the result entity's own records
    # ("how many customers have claims"): extra concepts only qualify
    # WHICH records count. When the measure belongs to another concept,
    # extra concepts may be bridges on the measure path and stay joins.
    qualifies_result_records = (
        intent == "list"
        or (
            intent in {"count", "aggregate"}
            and measure_concept is not None
            and measure_concept == result_concept
        )
    )

    if not qualifies_result_records:
        return []

    skipped = {result_concept} | filter_concepts | grouping_concepts

    return [concept for concept in concepts if concept not in skipped]
