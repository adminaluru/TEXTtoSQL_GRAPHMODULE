"""Tenant row scope (decision D-002).

The tenant is a logical identifier (AccessContext.tenant_id). Which physical
owner key a tenant owns, and which column carries that key in each table, is
explicit mapping data - never derived from names or ID formats.

POC rule: one company per tenant. TenantScopeMap enforces it in both
directions (a tenant owns exactly one key; a key belongs to one tenant).
Supporting several companies per tenant later changes owner_key to a set
without changing callers of TenantScopeResolver.
"""
from typing import Annotated, Union

from pydantic import BaseModel, ConfigDict, StrictInt, StringConstraints, model_validator

from hybrid_contracts import AccessContext

# Only AccessContext is public in hybrid_contracts; mirror its model rules here.
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
OwnerKey = Union[StrictInt, Identifier]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "1.0"


class TenantScopeError(PermissionError):
    """No trusted scope can be established; callers must fail closed."""


class UnknownTenantError(TenantScopeError):
    """The caller's active tenant has no configured scope (a permission problem)."""


class UnscopedTableError(TenantScopeError):
    """The table has no ownership mapping, so it cannot be limited to a tenant."""


class TenantOwnership(Contract):
    tenant_id: Identifier
    owner_key: OwnerKey


class OwnershipColumn(Contract):
    table: Identifier
    column: Identifier


class OwnershipLink(Contract):
    """Rows of `table` belong to the tenant that owns their linked row in `via`.

    For tables without their own owner column (e.g. claims owned through their
    invoice). The application must join `table` to `via` through one confirmed
    many-to-one relationship and scope `via`. Links are one level deep.
    """
    table: Identifier
    via: Identifier


class RowPredicate(Contract):
    """Equality predicate a SQL adapter must bind as a parameter."""
    table: Identifier
    column: Identifier
    value: OwnerKey


class TenantScopeMap(Contract):
    tenants: tuple[TenantOwnership, ...]
    columns: tuple[OwnershipColumn, ...]
    links: tuple[OwnershipLink, ...] = ()

    @model_validator(mode="after")
    def _one_company_per_tenant(self):
        tenant_ids = [t.tenant_id for t in self.tenants]
        if len(set(tenant_ids)) != len(tenant_ids):
            raise ValueError("A tenant may own exactly one company in this POC")
        owner_keys = [t.owner_key for t in self.tenants]
        if len(set(owner_keys)) != len(owner_keys):
            raise ValueError("A company may belong to only one tenant")
        tables = [c.table for c in self.columns]
        if len(set(tables)) != len(tables):
            raise ValueError("Each table needs exactly one ownership column")
        for link in self.links:
            if link.table in tables:
                raise ValueError("A table is owned either by its own column or through a link, not both")
            if link.via not in tables:
                raise ValueError("A link must point at a table with its own ownership column")
        linked = [link.table for link in self.links]
        if len(set(linked)) != len(linked):
            raise ValueError("Each table needs exactly one ownership link")
        return self


class TenantScopeResolver:
    def __init__(self, scope_map: TenantScopeMap):
        self._owners = {t.tenant_id: t.owner_key for t in scope_map.tenants}
        self._columns = {c.table: c.column for c in scope_map.columns}
        self._links = {link.table: link.via for link in scope_map.links}

    def link_for(self, table: str) -> str | None:
        """The table whose owner also owns rows of `table`, if ownership is linked."""
        return self._links.get(table)

    def predicates_for(self, access: AccessContext, table: str) -> tuple[RowPredicate, ...]:
        # Messages stay generic: they may reach users and must not reveal
        # tenant keys or physical schema.
        if access.tenant_id not in self._owners:
            raise UnknownTenantError("No data scope is configured for the active tenant.")
        if table not in self._columns:
            raise UnscopedTableError("This data cannot be scoped to the active tenant.")
        return (RowPredicate(table=table, column=self._columns[table],
                             value=self._owners[access.tenant_id]),)
