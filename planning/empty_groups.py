"""Keep groups that have no matching records (decision D-005).

A total grouped by an entity must list every entity, with 0 where it has
no measured records, instead of silently dropping it through INNER JOINs.

Applied only when it is provably correct:

- every non-currency grouping is on one table, and it is the base table,
  so LEFT JOINs outward from it keep each of its rows;
- no question filter is on another table (a WHERE predicate on the LEFT
  side would turn the LEFT JOIN back into an INNER JOIN). Company (tenant)
  restrictions on joined tables are moved into that table's JOIN ... ON
  clause instead: other companies' rows stay excluded, empty groups stay;
- existence conditions are anchored on the grouped table.

SUM and COUNT of no records are 0. AVG, MIN and MAX of no records have no
value, so they stay empty rather than becoming a misleading 0.

When the rule cannot be applied, the plan is unchanged and the result says
that empty groups are not listed.
"""
from planning.models import QueryPlan

EMPTY_GROUPS_OMITTED = "Groups with no matching records are not listed in this result."

ZERO_WHEN_EMPTY = {"SUM", "COUNT"}


class EmptyGroupPolicy:
    def apply(self, plan: QueryPlan) -> QueryPlan:
        if not plan.groupings or not plan.aggregations:
            return plan

        currency = {(p.table, p.column) for p in plan.projections
                    if plan.currency_alias and p.alias == plan.currency_alias}
        grouped = {g.table for g in plan.groupings if (g.table, g.column) not in currency}

        # A currency split alone is not a user grouping: a scalar total
        # per currency has no empty groups to report.
        if not grouped:
            return plan

        base = plan.tables[0]
        safe = (
            grouped == {base}
            and all(f.table == base or f.tenant_scope for f in plan.filters)
            and all(c.anchor_table == base for c in plan.existence_conditions)
        )

        result = plan.model_copy(deep=True)
        if not safe:
            if EMPTY_GROUPS_OMITTED not in result.result_notes:
                result.result_notes.append(EMPTY_GROUPS_OMITTED)
            return result

        introduced_by = _introducing_joins(base, result.joins)
        kept = []
        for f in result.filters:
            if f.table != base and f.tenant_scope:
                # Applied while joining, so it cannot remove empty groups.
                introduced_by[f.table].on_filters.append(f)
            else:
                kept.append(f)
        result.filters = kept

        for join in result.joins:
            join.join_type = "LEFT"
        for aggregation in result.aggregations:
            aggregation.empty_as_zero = aggregation.function in ZERO_WHEN_EMPTY
        return result


def _introducing_joins(base: str, joins) -> dict:
    """For each joined table, the join that connects it, walking out from the base."""
    introduced = {}
    connected = {base}
    remaining = list(joins)
    while remaining:
        for join in remaining:
            if join.left_table in connected and join.right_table not in connected:
                new = join.right_table
            elif join.right_table in connected and join.left_table not in connected:
                new = join.left_table
            else:
                continue
            introduced[new] = join
            connected.add(new)
            remaining.remove(join)
            break
        else:
            break
    return introduced
