"""Disclose records whose measured value is missing (decision D-006).

SUM / AVG / MIN / MAX ignore NULL values. "Total approved amount 6750.00"
silently omits the 9 claims that have no approved amount yet, so each such
aggregation also counts its measure records with a NULL value:

    COUNT(measure.pk) FILTER (WHERE measure.value IS NULL)

Counting the measure table's own key (not rows) keeps LEFT JOIN placeholder
rows of empty groups (D-005) from being reported as missing values.
"""
from planning.models import QueryPlan

DISCLOSED_FUNCTIONS = {"SUM", "AVG", "MIN", "MAX"}

UNCOUNTABLE = "Records without a value may not be included in this result."


class MissingValuePolicy:
    def __init__(self, ontology):
        self._ontology = ontology

    def apply(self, plan: QueryPlan) -> QueryPlan:
        targets = [a for a in plan.aggregations if a.function in DISCLOSED_FUNCTIONS]
        if not targets or all(a.missing_alias for a in targets):
            return plan
        result = plan.model_copy(deep=True)
        for aggregation in result.aggregations:
            if aggregation.function not in DISCLOSED_FUNCTIONS or aggregation.missing_alias:
                continue
            keys = [c.name for c in self._ontology.get_table_columns(aggregation.table)
                    if c.is_primary_key]
            if not keys:
                if UNCOUNTABLE not in result.result_notes:
                    result.result_notes.append(UNCOUNTABLE)
                continue
            # Any key column is non-NULL exactly when a measure row exists.
            aggregation.missing_key_column = keys[0]
            aggregation.missing_alias = f"{aggregation.alias}_missing"
        return result
