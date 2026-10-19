"""
Tenant isolation on EVERY table (fix found in the first live UI run, 2026-10-15).

Defect: only named records (a customer name) were limited to the caller's
company. "What is the total invoice amount?" for an Acme user returned
327,000.00 USD = Acme 129,000 + Nova 99,000 + Vertex 99,000.

Verified Golden Data (read-only SQL, 2026-10-15):

    company 527 (tenant-acme)  invoices USD 129000.00, INR 70800.00; claims 4500.00;
                               6 customers with claims; 9 customers, 3 without
                               payments; 24 open invoices
    company 528 (tenant-nova)  invoices USD 99000.00; claims 4500.00;
                               6 customers with claims; 18 open invoices
    invoices whose company differs from their customer's: 0
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from planning.empty_groups import EmptyGroupPolicy
from planning.errors import UnsupportedPredicateError
from planning.models import (QueryAggregation, QueryExistence, QueryFilter, QueryGrouping,
                             QueryJoin, QueryPlan, QueryProjection)
from planning.tenant_plan_scope import NOT_SCOPED, TenantPlanScope
from sql_compiler.compiler import DeterministicSQLCompiler

INV, CLAIM, CUST, PAY, ORPHAN = ("public.invoices", "public.claims", "public.customers",
                                  "public.payments", "public.orphans")


class Policy:
    """Synthetic policy: invoices/customers/payments owned directly, claims via invoices."""
    own = {INV: "company_id", CUST: "company_id", PAY: "company_id"}

    def __init__(self, denied=False):
        self.denied = denied

    def filters_for(self, table):
        if self.denied:
            raise PermissionError("No data scope is configured for the active tenant.")
        if table not in self.own:
            raise LookupError("unowned")
        return [QueryFilter(table=table, column=self.own[table], operator="=", value=527, tenant_scope=True)]

    def parent_for(self, table):
        return INV if table == CLAIM else None


def ontology(paths=None):
    o = Mock()
    o.get_table_columns.side_effect = lambda t: [SimpleNamespace(name="id", is_primary_key=True)]
    fk = SimpleNamespace(joins=[SimpleNamespace(source_table=CLAIM, target_table=INV, source_column="invoice_id",
                                                target_column="id", validation_status="confirmed")])
    o.find_physical_paths.return_value = [fk] if paths is None else paths
    return o


def scope(policy=None, paths=None):
    return TenantPlanScope(ontology(paths), policy or Policy())


def total(table, column="amount", tables=None, joins=()):
    return QueryPlan(intent="aggregate", tables=tables or [table], joins=list(joins),
                     aggregations=[QueryAggregation(function="SUM", table=table, column=column, alias="v")])


def company_filters(plan):
    return [(f.table, f.column, f.value) for f in plan.filters if f.tenant_scope]


def test_every_directly_owned_table_is_limited():
    plan = total(PAY, tables=[CUST, PAY], joins=[QueryJoin(left_table=PAY, left_column="customer_id",
                                                           right_table=CUST, right_column="id")])
    scoped = scope().apply(plan)
    assert company_filters(scoped) == [(CUST, "company_id", 527), (PAY, "company_id", 527)]
    assert plan.filters == []                        # caller's plan untouched


def test_whole_table_total_is_limited_not_global():
    sql = DeterministicSQLCompiler().compile(scope().apply(total(INV)))
    assert "WHERE t0.company_id = %s" in sql.sql and sql.parameters == [527]


def test_linked_table_is_scoped_through_its_owner():
    scoped = scope().apply(total(CLAIM, "claim_amount"))
    assert scoped.tables == [CLAIM, INV]
    [join] = scoped.joins
    assert (join.left_table, join.left_column, join.right_table, join.join_type) == (CLAIM, "invoice_id", INV, "INNER")
    assert company_filters(scoped) == [(INV, "company_id", 527)]


def test_existing_owner_join_is_reused():
    existing = QueryJoin(left_table=INV, left_column="id", right_table=CLAIM, right_column="invoice_id")
    scoped = scope().apply(total(CLAIM, "claim_amount", tables=[INV, CLAIM], joins=[existing]))
    assert scoped.joins == [existing]


def test_owner_reached_through_a_different_relationship_is_refused():
    other = QueryJoin(left_table=CLAIM, left_column="credit_invoice_id", right_table=INV, right_column="id")
    with pytest.raises(UnsupportedPredicateError, match="cannot be limited"):
        scope().apply(total(CLAIM, "claim_amount", tables=[CLAIM, INV], joins=[other]))


@pytest.mark.parametrize("paths", [[], [SimpleNamespace(joins=[SimpleNamespace(
    source_table=CLAIM, target_table=INV, source_column="invoice_id", target_column="id",
    validation_status="proposed")])]])
def test_link_without_one_confirmed_join_is_refused(paths):
    with pytest.raises(UnsupportedPredicateError):
        scope(paths=paths).apply(total(CLAIM, "claim_amount"))


def test_table_with_no_owner_is_refused_never_read_unrestricted():
    with pytest.raises(UnsupportedPredicateError) as error:
        scope().apply(total(ORPHAN))
    assert str(error.value) == NOT_SCOPED


def test_caller_without_any_scope_is_denied_not_unsupported():
    with pytest.raises(PermissionError):
        scope(Policy(denied=True)).apply(total(INV))


def test_existence_tables_are_limited_inside_the_condition():
    plan = QueryPlan(intent="aggregate", tables=[CUST],
                     aggregations=[QueryAggregation(function="COUNT", table=CUST, column="id", alias="n")],
                     existence_conditions=[QueryExistence(
                         anchor_table=CUST, tables=[INV, CLAIM],
                         joins=[QueryJoin(left_table=INV, left_column="customer_id", right_table=CUST, right_column="id"),
                                QueryJoin(left_table=CLAIM, left_column="invoice_id", right_table=INV, right_column="id")])])
    scoped = scope().apply(plan)
    assert company_filters(scoped) == [(CUST, "company_id", 527)]
    assert [(f.table, f.value) for f in scoped.existence_conditions[0].filters] == [(INV, 527)]


def test_linked_table_correlated_to_the_scoped_outer_row_is_accepted():
    plan = total(INV)
    plan.existence_conditions.append(QueryExistence(
        anchor_table=INV, tables=[CLAIM],
        joins=[QueryJoin(left_table=CLAIM, left_column="invoice_id", right_table=INV, right_column="id")]))
    scoped = scope().apply(plan)
    assert company_filters(scoped) == [(INV, "company_id", 527)]


def test_linked_table_inside_a_condition_without_its_owner_is_refused():
    plan = total(CUST)
    plan.existence_conditions.append(QueryExistence(anchor_table=CUST, tables=[CLAIM], joins=[]))
    with pytest.raises(UnsupportedPredicateError):
        scope().apply(plan)


def test_restrictions_are_not_duplicated():
    plan = total(INV)
    plan.filters.append(QueryFilter(table=INV, column="company_id", operator="=", value=527, tenant_scope=True))
    assert len(scope().apply(plan).filters) == 1


def test_empty_groups_keep_zero_rows_and_still_exclude_other_companies():
    plan = QueryPlan(
        intent="aggregate", tables=[CUST, PAY],
        joins=[QueryJoin(left_table=PAY, left_column="customer_id", right_table=CUST, right_column="id",
                         validation_status="confirmed")],
        projections=[QueryProjection(table=CUST, column="name", alias="customer_name")],
        groupings=[QueryGrouping(table=CUST, column="id"), QueryGrouping(table=CUST, column="name")],
        aggregations=[QueryAggregation(function="SUM", table=PAY, column="amount", alias="v")])
    grouped = EmptyGroupPolicy().apply(scope().apply(plan))
    assert grouped.result_notes == []                                 # zero rows are kept
    assert [(f.table, f.value) for f in grouped.filters] == [(CUST, 527)]
    assert [(f.table, f.value) for f in grouped.joins[0].on_filters] == [(PAY, 527)]
    compiled = DeterministicSQLCompiler().compile(grouped)
    sql = " ".join(compiled.sql.split())
    assert "LEFT JOIN public.payments AS t1 ON t1.customer_id = t0.id AND t1.company_id = %s" in sql
    assert compiled.parameters == [527, 527]                          # ON parameter first, then WHERE


def test_join_conditions_must_be_equality_on_the_joined_table():
    plan = total(PAY, tables=[CUST, PAY], joins=[QueryJoin(
        left_table=PAY, left_column="customer_id", right_table=CUST, right_column="id",
        on_filters=[QueryFilter(table=CUST, column="company_id", operator="=", value=1)])])
    with pytest.raises(ValueError, match="joined table"):
        DeterministicSQLCompiler().compile(plan)


# ----------------------------------------------------------- live, read-only

def live(tenant, response):
    from pathlib import Path

    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from api.identity_setup import tenant_resolver_from_file
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor
    from hybrid_contracts import AccessContext
    from planning.tenant_entity_scope import TenantEntityScopePolicy

    resolver = tenant_resolver_from_file(str(Path(__file__).parents[1] / "docs" / "examples" / "tenant_scope.dev.json"))
    access = AccessContext(subject_id="user:t", tenant_id=tenant, policy_version="test")
    neo4j = Neo4jClient()
    try:
        agent = build_research_agent(chat_model=MockChatModel(structured_response=response), neo4j_client=neo4j,
                                     postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100),
                                     entity_scope_policy=TenantEntityScopePolicy(resolver, access))
        return agent.ask("probe")
    finally:
        neo4j.close()


def question(intent, concepts, result, measure, attribute=None, aggregation=None, group=(), entities=()):
    return {"intent": intent, "concepts": concepts, "result_concept": result, "measure_concept": measure,
            "measure_attribute": attribute, "group_by_concepts": list(group), "entities": list(entities),
            "aggregation": aggregation}


TOTAL_INVOICES = question("aggregate", ["Invoice"], "Invoice", "Invoice", "invoice amount", "SUM")


@pytest.mark.integration
@pytest.mark.parametrize("tenant,expected", [
    ("tenant-acme", {"USD": Decimal("129000.00"), "INR": Decimal("70800.00")}),
    ("tenant-nova", {"USD": Decimal("99000.00")}),
])
def test_live_total_invoice_amount_is_per_company(tenant, expected):
    answer = live(tenant, TOTAL_INVOICES)
    if isinstance(answer.data, list):
        got = {r["currency"]: Decimal(str(r["sum_amount"])) for r in answer.data}
    else:
        got = {answer.data["currency"]: Decimal(str(answer.data["value"]))}
    assert got == expected


@pytest.mark.integration
def test_live_claims_total_is_limited_through_invoices():
    answer = live("tenant-acme", question("aggregate", ["InvoiceClaim"], "InvoiceClaim", "InvoiceClaim",
                                          "claim amount", "SUM"))
    assert Decimal(str(answer.data["value"])) == Decimal("4500.00")


@pytest.mark.integration
def test_live_open_invoice_count_is_per_company():
    answer = live("tenant-acme", question("count", ["Invoice"], "Invoice", "Invoice", None, "COUNT",
                                          entities=[{"concept": "Invoice", "attribute": "invoice status",
                                                     "value": "open"}]))
    assert answer.data["value"] == 24


@pytest.mark.integration
def test_live_grouped_payments_list_only_own_customers_with_zeros():
    answer = live("tenant-acme", question("aggregate", ["Customer", "PaymentReceipt"], "Customer",
                                          "PaymentReceipt", "payment amount", "SUM", group=["Customer"]))
    names = {r["customer_name"] for r in answer.data}
    assert len(answer.data) == 9
    assert not any(n.startswith(("Nova", "Vertex")) for n in names)
    zero = [r for r in answer.data if Decimal(str(r["sum_payment_amount"])) == 0]
    assert len(zero) == 3


@pytest.mark.integration
def test_live_customers_with_claims_are_counted_per_company():
    answer = live("tenant-nova", question("count", ["Customer", "Invoice", "InvoiceClaim"], "Customer",
                                          "Customer", None, "COUNT"))
    assert answer.data["value"] == 6
