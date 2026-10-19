import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from hybrid_contracts import AccessContext
from tenant_scope import (OwnershipColumn, RowPredicate, TenantOwnership,
                          TenantScopeError, TenantScopeMap, TenantScopeResolver)

CUSTOMERS = "public.customers"


def access(tenant="tenant-a"):
    return AccessContext(subject_id="u1", tenant_id=tenant, policy_version="1")


def scope_map(tenants=(("tenant-a", 527), ("tenant-b", 528)), columns=((CUSTOMERS, "company_id"),)):
    return TenantScopeMap(
        tenants=tuple(TenantOwnership(tenant_id=t, owner_key=k) for t, k in tenants),
        columns=tuple(OwnershipColumn(table=t, column=c) for t, c in columns))


def test_logical_tenant_maps_to_its_owner_predicate():
    resolver = TenantScopeResolver(scope_map())
    assert resolver.predicates_for(access("tenant-b"), CUSTOMERS) == (
        RowPredicate(table=CUSTOMERS, column="company_id", value=528),)


def test_tenant_id_is_not_the_physical_key():
    # The logical tenant "527" is unrelated to company 527 unless mapped.
    resolver = TenantScopeResolver(scope_map(tenants=(("527", 529),)))
    assert resolver.predicates_for(access("527"), CUSTOMERS)[0].value == 529


def test_unknown_tenant_fails_closed():
    with pytest.raises(TenantScopeError, match="active tenant"):
        TenantScopeResolver(scope_map()).predicates_for(access("tenant-x"), CUSTOMERS)


def test_table_without_ownership_column_fails_closed():
    with pytest.raises(TenantScopeError, match="cannot be scoped"):
        TenantScopeResolver(scope_map()).predicates_for(access(), "public.invoices")


@pytest.mark.parametrize("tenants", [
    (("tenant-a", 527), ("tenant-a", 528)),   # one tenant, two companies
    (("tenant-a", 527), ("tenant-b", 527)),   # one company, two tenants
])
def test_one_company_per_tenant_is_enforced(tenants):
    with pytest.raises(ValidationError):
        scope_map(tenants=tenants)


def test_table_with_two_ownership_columns_is_rejected():
    with pytest.raises(ValidationError):
        scope_map(columns=((CUSTOMERS, "company_id"), (CUSTOMERS, "legal_entity_id")))


@pytest.mark.parametrize("key", [True, None, ""])
def test_invalid_owner_key_is_rejected(key):
    with pytest.raises(ValidationError):
        TenantOwnership(tenant_id="tenant-a", owner_key=key)


def test_error_messages_do_not_reveal_keys_or_schema():
    resolver = TenantScopeResolver(scope_map())
    for call in (lambda: resolver.predicates_for(access("tenant-x"), CUSTOMERS),
                 lambda: resolver.predicates_for(access(), "public.invoices")):
        with pytest.raises(TenantScopeError) as error:
            call()
        assert not any(s in str(error.value) for s in ("527", "company_id", "public."))


def test_package_imports_only_portable_dependencies():
    allowed = {"typing", "pydantic", "hybrid_contracts"}
    src = Path(__file__).parents[1] / "src" / "tenant_scope"
    for path in src.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert roots <= allowed, (path.name, roots)


# 0.2.0: ownership through a linked table (e.g. claims through their invoice)

from tenant_scope import OwnershipLink


def linked_map(links=(("public.claims", "public.invoices"),), columns=(("public.invoices", "company_id"),)):
    return TenantScopeMap(
        tenants=(TenantOwnership(tenant_id="tenant-a", owner_key=527),),
        columns=tuple(OwnershipColumn(table=t, column=c) for t, c in columns),
        links=tuple(OwnershipLink(table=t, via=v) for t, v in links))


def test_linked_table_reports_its_owner_table_and_has_no_own_predicate():
    resolver = TenantScopeResolver(linked_map())
    assert resolver.link_for("public.claims") == "public.invoices"
    assert resolver.link_for("public.invoices") is None
    with pytest.raises(TenantScopeError):
        resolver.predicates_for(access(), "public.claims")


@pytest.mark.parametrize("links,columns", [
    ((("public.claims", "public.other"),), (("public.invoices", "company_id"),)),          # via unmapped
    ((("public.invoices", "public.invoices"),), (("public.invoices", "company_id"),)),     # both kinds
    ((("public.claims", "public.invoices"), ("public.claims", "public.invoices")),
     (("public.invoices", "company_id"),)),                                                 # duplicate
])
def test_invalid_links_are_rejected(links, columns):
    with pytest.raises(ValidationError):
        linked_map(links=links, columns=columns)


def test_maps_without_links_are_unchanged():
    assert TenantScopeResolver(scope_map()).link_for(CUSTOMERS) is None
