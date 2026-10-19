"""
Status / exact-value conditions (Step 3, item B).

Defects found 2026-10-15:

    invoice status = 'open'    -> "count is 0"   (stored value is OPEN; truth 60)
    invoice status = 'Unpaid'  -> "count is 0"   (not a value at all)
    "invoice status" resolved to inv_status in most processes but was
    refused as ambiguous in others (see test_column_score_determinism.py)
    answer read "... records for OPEN ..." as if OPEN were a customer

Verified Golden Data (read-only SQL, 2026-10-15):

    inv_status          OPEN 60, CLOSE 18
    transaction_status  OVERDUE 36, PAID 18, PENDING 24
    LE-1001 OPEN invoices 3
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from planning.models import QueryExistence, QueryFilter, QueryPlan
from planning.value_domain_guard import ValueDomainGuard

INV = "public.invoices"


def columns():
    return [SimpleNamespace(name="id", is_primary_key=True, category="identifier"),
            SimpleNamespace(name="inv_status", is_primary_key=False, category="dimension"),
            SimpleNamespace(name="invoice_num", is_primary_key=False, category="business identifier"),
            SimpleNamespace(name="customer_id", is_primary_key=False, category="foreign key")]


def guard(values=("CLOSE", "OPEN"), truncated=False, scope=None, max_values=50):
    ontology = Mock()
    ontology.get_table_columns.return_value = columns()
    probe = Mock()
    probe.run.return_value = SimpleNamespace(
        rows=[{"domain_value": v, "domain_count": 1} for v in values], truncated=truncated)
    policy = None
    if scope:
        policy = Mock()
        policy.filters_for.side_effect = lambda t: [QueryFilter(table=t, column="company_id",
                                                                operator="=", value=527)]
    return ValueDomainGuard(ontology, probe, scope_policy=policy, max_values=max_values), probe


def plan(value, column="inv_status", operator="="):
    return QueryPlan(intent="aggregate", tables=[INV], filters=[
        QueryFilter(table=INV, column=column, operator=operator, value=value)])


@pytest.mark.parametrize("given", ["open", "Open", " OPEN ", "OPEN"])
def test_case_and_spacing_variants_use_the_stored_value(given):
    g, _ = guard()
    original = plan(given)
    result = g.resolve(original)
    assert result.clarification is None
    assert result.plan.filters[0].value == "OPEN"
    assert original.filters[0].value == given


def test_unknown_value_is_clarified_with_known_values():
    g, _ = guard()
    result = g.resolve(plan("Unpaid"))
    assert result.clarification == (
        "“Unpaid” doesn't match any known value for this condition. Known values: CLOSE, OPEN.")
    assert "inv_status" not in result.clarification


def test_values_differing_only_by_case_are_clarified():
    g, _ = guard(values=("OPEN", "Open"))
    assert "more than one stored value" in g.resolve(plan("open")).clarification


@pytest.mark.parametrize("column", ["invoice_num", "customer_id", "id"])
def test_identifier_columns_stay_exact_and_are_not_looked_up(column):
    g, probe = guard()
    original = plan("syninv000101", column=column)
    assert g.resolve(original).plan == original
    probe.run.assert_not_called()


def test_large_domain_stays_exact():
    g, _ = guard(values=[f"V{i}" for i in range(4)], max_values=3)
    original = plan("v1")
    assert g.resolve(original).plan == original


def test_name_and_non_text_conditions_are_untouched():
    g, probe = guard()
    for original in (plan("%Blue%", operator="ILIKE"), plan(527)):
        assert g.resolve(original).plan == original
    probe.run.assert_not_called()


def test_lookup_is_bounded_and_scoped():
    g, probe = guard(scope=True, max_values=50)
    g.resolve(plan("open"))
    lookup = probe.run.call_args.args[0]
    assert lookup.limit == 51
    assert [(f.column, f.value) for f in lookup.filters] == [("company_id", 527)]


def test_existence_condition_values_are_checked_too():
    g, _ = guard()
    original = QueryPlan(intent="list", tables=["public.customers"], existence_conditions=[
        QueryExistence(anchor_table="public.customers", tables=[INV],
                       filters=[QueryFilter(table=INV, column="inv_status", operator="=", value="open")])])
    assert g.resolve(original).plan.existence_conditions[0].filters[0].value == "OPEN"


# ----------------------------------------------------------
# Live, read-only
# ----------------------------------------------------------

def live(value, name=None):
    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor

    entities = [{"concept": "Invoice", "attribute": "invoice status", "value": value}]
    concepts = ["Invoice"]
    if name:
        entities.insert(0, {"concept": "Customer", "attribute": "customer name", "value": name})
        concepts = ["Customer", "Invoice"]
    response = {"intent": "count", "concepts": concepts, "result_concept": concepts[0],
                "measure_concept": "Invoice", "measure_attribute": None, "group_by_concepts": [],
                "entities": entities, "aggregation": "COUNT"}
    neo4j = Neo4jClient()
    try:
        agent = build_research_agent(chat_model=MockChatModel(structured_response=response),
                                     neo4j_client=neo4j,
                                     postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000,
                                                                          max_rows=100))
        return agent.ask("How many open invoices?")
    finally:
        neo4j.close()


@pytest.mark.integration
@pytest.mark.parametrize("value", ["open", "OPEN", "Open"])
def test_live_open_invoices_any_case(value):
    answer = live(value)
    assert answer.result_type == "scalar"
    assert answer.data["value"] == 60
    assert answer.answer == f"The count of invoice records with invoice status {value} is 60."


@pytest.mark.integration
def test_live_unknown_status_is_clarified_not_zero():
    answer = live("Unpaid")
    assert answer.result_type == "clarification"
    assert "Known values: CLOSE, OPEN" in answer.answer


@pytest.mark.integration
def test_live_status_with_customer():
    answer = live("open", name="Blue Prism Technologies LE-1001")
    assert answer.data["value"] == 3
    assert answer.answer == ("The count of invoice records with invoice status open "
                             "for Blue Prism Technologies LE-1001 is 3.")
