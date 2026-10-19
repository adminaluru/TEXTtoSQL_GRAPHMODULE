"""Adapter from the portable tenant_scope package to the planning guards.

Converts tenant RowPredicates into bound QueryFilters (decision D-002) and
reports linked ownership (e.g. claims owned through their invoice).
The AccessContext must come from verified identity; this adapter does not
authenticate the caller.
"""
from hybrid_contracts import AccessContext
from tenant_scope import TenantScopeResolver, UnscopedTableError

from planning.models import QueryFilter


class TenantEntityScopePolicy:
    def __init__(self, resolver: TenantScopeResolver, access: AccessContext):
        self._resolver = resolver
        self._access = access

    def filters_for(self, table: str) -> list[QueryFilter]:
        """
        Company filters for a table with its own owner column.

        Raises LookupError for a table without an owner mapping (the data
        is not available), and PermissionError when the caller's tenant
        has no scope at all (denied).
        """
        try:
            predicates = self._resolver.predicates_for(self._access, table)
        except UnscopedTableError as exc:
            raise LookupError(str(exc)) from exc
        return [
            QueryFilter(table=p.table, column=p.column, operator="=", value=p.value,
                        tenant_scope=True)
            for p in predicates
        ]

    def parent_for(self, table: str) -> str | None:
        return self._resolver.link_for(table)
