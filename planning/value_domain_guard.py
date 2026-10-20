"""Check exact-value text conditions against the values the column holds.

A condition may arrive as status = 'open' while the data holds 'OPEN';
'unpaid' may not be a stored value at all. Exact equality would
then answer 0 with confidence. Before answering, each exact text condition
on a small-domain column is looked up (bounded, inside the caller's scope):

- one case-insensitive match: the stored value is used ("open" -> "OPEN");
- no match, or several differing only by case: clarification listing the
  known values;
- more distinct values than max_values (identifiers, free text): left as
  exact equality, because listing them is neither useful nor bounded.

Identifier and key columns are never looked up; they must match exactly.
"""
from dataclasses import dataclass

from planning.models import QueryAggregation, QueryGrouping, QueryPlan, QueryProjection

NOT_A_KNOWN_VALUE = "“{value}” doesn't match any known value for this condition. Known values: {known}."
CASE_AMBIGUOUS = "“{value}” matches more than one stored value: {known}. Please say which one you mean."

SKIPPED_CATEGORIES = ("identifier", "key", "reference")


@dataclass(frozen=True)
class ValueDomainResult:
    plan: QueryPlan
    clarification: str | None = None


class ValueDomainGuard:
    def __init__(self, ontology, probe_runner, scope_policy=None, max_values: int = 50):
        if max_values < 1:
            raise ValueError("max_values must be positive")
        self._ontology = ontology
        self._probe = probe_runner
        self._scope_policy = scope_policy
        self._max_values = max_values

    def resolve(self, plan: QueryPlan) -> ValueDomainResult:
        resolved = plan.model_copy(deep=True)
        scopes = [resolved.filters]
        scopes.extend(condition.filters for condition in resolved.existence_conditions)
        for filters in scopes:
            for condition in filters:
                if condition.operator != "=" or not isinstance(condition.value, str):
                    continue
                if not self._is_domain_column(condition.table, condition.column):
                    continue
                known = self._known_values(condition.table, condition.column)
                if known is None:
                    continue
                wanted = condition.value.strip().casefold()
                matches = [v for v in known if v.strip().casefold() == wanted]
                if len(matches) == 1:
                    condition.value = matches[0]
                    continue
                listed = ", ".join(matches or known) or "none"
                template = CASE_AMBIGUOUS if matches else NOT_A_KNOWN_VALUE
                return ValueDomainResult(plan, template.format(value=condition.value.strip(),
                                                               known=listed))
        return ValueDomainResult(resolved)

    def holds(self, table: str, column: str, value) -> bool | None:
        """
        Whether the column holds this value (case-insensitive), inside the
        caller's scope. None when it cannot tell: not a text value, not a
        small-domain column, or too many distinct values to list.
        """
        if not isinstance(value, str) or not self._is_domain_column(table, column):
            return None
        known = self._known_values(table, column)
        if known is None:
            return None
        wanted = value.strip().casefold()
        return any(v.strip().casefold() == wanted for v in known)

    def _is_domain_column(self, table: str, column: str) -> bool:
        for c in self._ontology.get_table_columns(table):
            if c.name == column:
                category = (c.category or "").lower()
                return not c.is_primary_key and not any(s in category for s in SKIPPED_CATEGORIES)
        return False

    def _known_values(self, table: str, column: str) -> list[str] | None:
        scope = list(self._scope_policy.filters_for(table)) if self._scope_policy else []
        if any(f.table != table for f in scope):
            raise ValueError("Scope policy returned a predicate for another table.")
        probe = QueryPlan(
            intent="aggregate", tables=[table], filters=scope,
            projections=[QueryProjection(table=table, column=column, alias="domain_value")],
            groupings=[QueryGrouping(table=table, column=column)],
            aggregations=[QueryAggregation(function="COUNT", table=table, column=column,
                                           alias="domain_count")],
            limit=self._max_values + 1,
        )
        result = self._probe.run(probe)
        if result.truncated or len(result.rows) > self._max_values:
            return None
        return sorted(str(r["domain_value"]) for r in result.rows if r["domain_value"] is not None)
