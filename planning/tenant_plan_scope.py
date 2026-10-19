"""Limit every table in a question to the caller's company (D-002, tenant isolation).

Before this, only a *named* record (e.g. a customer name) was restricted, so
"total invoice amount" added up every company's invoices. Now every table the
query reads gets the company restriction:

- a table with its own owner column is filtered on it;
- a table owned through a linked table (e.g. claims through their invoice)
  is joined to that table through one confirmed many-to-one relationship,
  and the linked table is filtered;
- a table with no known owner is refused: never read unrestricted.

Inside EXISTS conditions the same rules apply; a linked table there must be
joined to its owner inside the condition, or correlated to the scoped outer
row.

The policy is injected (planning/tenant_entity_scope.py adapts the portable
tenant_scope package); this module knows no table names.
"""
from typing import Protocol

from planning.errors import UnsupportedPredicateError
from planning.models import QueryFilter, QueryJoin, QueryPlan

NOT_SCOPED = "This information cannot be limited to your company yet, so it is not available."


class RowScopePolicy(Protocol):
    def filters_for(self, table: str) -> list[QueryFilter]:
        """
        Company filters for a table with its own owner column.

        Raises LookupError when the table has no owner (-> not available),
        PermissionError when the caller has no scope at all (-> denied).
        """

    def parent_for(self, table: str) -> str | None:
        """The table that owns rows of `table`, when ownership is linked."""


class TenantPlanScope:
    def __init__(self, ontology, policy: RowScopePolicy):
        self._ontology = ontology
        self._policy = policy

    def apply(self, plan: QueryPlan) -> QueryPlan:
        scoped = plan.model_copy(deep=True)

        pending = list(scoped.tables)
        done: set[str] = set()
        while pending:
            table = pending.pop(0)
            if table in done:
                continue
            done.add(table)
            parent = self._policy.parent_for(table)
            if parent is None:
                self._add(scoped.filters, self._filters(table))
                continue
            link = self._link_join(table, parent)
            if parent in scoped.tables:
                if not any(_same_join(j, link) for j in scoped.joins):
                    raise UnsupportedPredicateError(NOT_SCOPED)
            else:
                scoped.tables.append(parent)
                scoped.joins.append(link)
            pending.append(parent)

        for condition in scoped.existence_conditions:
            for table in condition.tables:
                parent = self._policy.parent_for(table)
                if parent is None:
                    self._add(condition.filters, self._filters(table))
                    continue
                link = self._link_join(table, parent)
                linked_inside = parent in condition.tables
                # Correlated to the outer row, which is itself scoped above.
                correlated = parent == condition.anchor_table
                if not ((linked_inside or correlated)
                        and any(_same_join(j, link) for j in condition.joins)):
                    raise UnsupportedPredicateError(NOT_SCOPED)

        return scoped

    def _filters(self, table: str) -> list[QueryFilter]:
        # A caller without any scope (PermissionError) is denied by the
        # layer above; only an unowned table becomes "not available".
        try:
            filters = self._policy.filters_for(table)
        except LookupError as exc:
            raise UnsupportedPredicateError(NOT_SCOPED) from exc
        if not filters or any(f.table != table for f in filters):
            raise UnsupportedPredicateError(NOT_SCOPED)
        return [f.model_copy(update={"tenant_scope": True}) for f in filters]

    @staticmethod
    def _add(target: list[QueryFilter], filters: list[QueryFilter]) -> None:
        for f in filters:
            if not any((g.table, g.column, g.operator, g.value) == (f.table, f.column, f.operator, f.value)
                       for g in target):
                target.append(f)

    def _link_join(self, table: str, parent: str) -> QueryJoin:
        # One direct, confirmed join from the table's foreign key to the
        # owner's single-column key: each row has at most one owner row.
        keys = [c.name for c in self._ontology.get_table_columns(parent) if c.is_primary_key]
        joins = [
            path.joins[0]
            for path in self._ontology.find_physical_paths(table, parent, max_hops=1)
            if len(path.joins) == 1
            and path.joins[0].source_table == table
            and path.joins[0].target_table == parent
            and path.joins[0].validation_status == "confirmed"
            and [path.joins[0].target_column] == keys
        ]
        if len(joins) != 1:
            raise UnsupportedPredicateError(NOT_SCOPED)
        return QueryJoin(left_table=table, left_column=joins[0].source_column,
                         right_table=parent, right_column=joins[0].target_column,
                         join_type="INNER", validation_status="confirmed")


def _same_join(existing: QueryJoin, expected: QueryJoin) -> bool:
    forward = (existing.left_table, existing.left_column, existing.right_table, existing.right_column)
    backward = (existing.right_table, existing.right_column, existing.left_table, existing.left_column)
    target = (expected.left_table, expected.left_column, expected.right_table, expected.right_column)
    return target in (forward, backward)
