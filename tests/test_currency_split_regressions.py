"""
A3.2 / D-004: monetary amounts are never combined across currencies.

Defect found by the Step 3 probe (2026-10-15):

    "What is the total invoice amount?"
    answered  "The total invoice amount is 397800.00."
    truth     327000.00 USD + 70800.00 INR (74 USD and 4 INR invoices)

Verified Golden Data (read-only SQL, 2026-10-15):

    invoices by currency     USD 327000.00 (74), INR 70800.00 (4)
    INR invoices             Bharat Industrial Systems 35400.00,
                             Horizon India Enterprises 35400.00
    payments (all via USD invoices)  72000.00 (36)
"""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from answering.answer_service import AnswerService
from nlu.models import QuestionAnalysis
from planning.currency_split import (CurrencySource, CurrencySplitter,
                                     load_currency_sources)
from planning.errors import UnsupportedPredicateError
from planning.models import QueryAggregation, QueryJoin, QueryPlan, QueryProjection
from result_analysis.analyzer import ResultAnalyzer

INVOICES, PAYMENTS, CUSTOMERS = "public.invoices", "public.payments", "public.customers"

SOURCES = {
    INVOICES: CurrencySource(column="currency_type"),
    PAYMENTS: CurrencySource(via=INVOICES),
}


def col(name, category="dimension", pk=False):
    return SimpleNamespace(name=name, category=category, is_primary_key=pk)


COLUMNS = {
    INVOICES: [col("id", "identifier", True), col("amount", "measure / financial"),
               col("currency_type")],
    PAYMENTS: [col("id", "identifier", True), col("payment_amount", "measure / financial"),
               col("invoice_id", "foreign key"), col("quantity", "measure")],
    CUSTOMERS: [col("id", "identifier", True), col("name")],
}


def fk(status="confirmed", target_column="id"):
    return SimpleNamespace(joins=[SimpleNamespace(
        source_table=PAYMENTS, target_table=INVOICES, source_column="invoice_id",
        target_column=target_column, validation_status=status)])


def splitter(paths=None, sources=SOURCES):
    ontology = Mock()
    ontology.get_table_columns.side_effect = lambda t: COLUMNS[t]
    ontology.find_physical_paths.return_value = [fk()] if paths is None else paths
    return CurrencySplitter(ontology, sources)


def agg_plan(table, column, function="SUM", tables=None, joins=()):
    return QueryPlan(intent="aggregate", tables=tables or [table], joins=list(joins),
                     aggregations=[QueryAggregation(function=function, table=table, column=column,
                                                    alias=f"{function.lower()}_{column}")])


def test_own_currency_column_groups_and_projects_currency():
    plan = splitter().apply(agg_plan(INVOICES, "amount"))
    assert [(g.table, g.column) for g in plan.groupings] == [(INVOICES, "currency_type")]
    assert [(p.table, p.column, p.alias) for p in plan.projections] == [
        (INVOICES, "currency_type", "currency")]
    assert plan.currency_alias == "currency"
    assert plan.tables == [INVOICES] and not plan.joins


def test_linked_invoice_currency_adds_one_confirmed_left_join():
    plan = splitter().apply(agg_plan(PAYMENTS, "payment_amount"))
    assert plan.tables == [PAYMENTS, INVOICES]
    [join] = plan.joins
    assert (join.left_table, join.left_column, join.right_table, join.right_column,
            join.join_type, join.validation_status) == (
        PAYMENTS, "invoice_id", INVOICES, "id", "LEFT", "confirmed")


@pytest.mark.parametrize("existing", [
    QueryJoin(left_table=PAYMENTS, left_column="invoice_id", right_table=INVOICES, right_column="id"),
    QueryJoin(left_table=INVOICES, left_column="id", right_table=PAYMENTS, right_column="invoice_id"),
])
def test_existing_identical_join_is_reused(existing):
    original = agg_plan(PAYMENTS, "payment_amount", tables=[PAYMENTS, INVOICES], joins=[existing])
    plan = splitter().apply(original)
    assert plan.tables == [PAYMENTS, INVOICES] and plan.joins == [existing]


def test_currency_table_reached_by_another_relationship_is_refused():
    other = QueryJoin(left_table=PAYMENTS, left_column="refund_invoice_id",
                      right_table=INVOICES, right_column="id")
    with pytest.raises(UnsupportedPredicateError, match="currency"):
        splitter().apply(agg_plan(PAYMENTS, "payment_amount", tables=[PAYMENTS, INVOICES],
                                  joins=[other]))


@pytest.mark.parametrize("paths", [[], [fk(status="proposed")], [fk(target_column="code")], [fk(), fk()]])
def test_currency_link_needs_one_confirmed_join_to_the_key(paths):
    with pytest.raises(UnsupportedPredicateError, match="currency"):
        splitter(paths=paths).apply(agg_plan(PAYMENTS, "payment_amount"))


def test_financial_measure_without_currency_source_is_refused():
    with pytest.raises(UnsupportedPredicateError, match="currency"):
        splitter(sources={}).apply(agg_plan(INVOICES, "amount"))


def test_amounts_from_different_tables_are_refused():
    plan = agg_plan(INVOICES, "amount")
    plan.aggregations.append(QueryAggregation(function="SUM", table=PAYMENTS,
                                              column="payment_amount", alias="sum_payment"))
    with pytest.raises(UnsupportedPredicateError, match="different kinds"):
        splitter().apply(plan)


@pytest.mark.parametrize("function,column,table", [
    ("COUNT", "id", INVOICES),               # counts carry no currency
    ("SUM", "quantity", PAYMENTS),           # non-financial measure
])
def test_non_monetary_aggregates_are_unchanged(function, column, table):
    original = agg_plan(table, column, function)
    assert splitter().apply(original) == original


@pytest.mark.parametrize("function", ["SUM", "AVG", "MIN", "MAX"])
def test_every_monetary_function_is_split(function):
    assert splitter().apply(agg_plan(INVOICES, "amount", function)).currency_alias == "currency"


def test_alias_collision_fails_closed():
    plan = agg_plan(INVOICES, "amount")
    plan.projections.append(QueryProjection(table=INVOICES, column="currency_type", alias="currency"))
    with pytest.raises(ValueError, match="collides"):
        splitter().apply(plan)


def test_loader_rejects_link_to_table_without_currency_column(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({PAYMENTS: {"via": INVOICES}}), encoding="utf-8")
    with pytest.raises(ValueError, match="without a currency column"):
        load_currency_sources(path)


def test_shipped_sources_cover_the_reviewed_tables():
    sources = load_currency_sources()
    assert sources["public.erp_payment_receipt"].via == "public.erp_invoice_details"
    assert sources["public.d_claim_invoice"].via == "public.erp_invoice_details"
    assert sources["public.erp_invoice_details"].column == "currency_type"


# ----------------------------------------------------------
# Result shape and wording
# ----------------------------------------------------------

def answer_for(rows):
    plan = splitter().apply(agg_plan(INVOICES, "amount"))
    execution = SimpleNamespace(rows=rows, row_count=len(rows), truncated=False,
                                columns=["currency", "sum_amount"])
    analyzer = ResultAnalyzer()
    analyzer._validate_execution_result = lambda e: None
    result = analyzer.analyze(execution, plan)
    analysis = QuestionAnalysis(intent="aggregate", concepts=["Invoice"], measure_concept="Invoice",
                                measure_attribute="invoice amount", aggregation="SUM")
    return AnswerService().build(analysis, result)


def test_single_currency_stays_scalar_and_names_its_currency():
    answer = answer_for([{"currency": "USD", "sum_amount": Decimal("4000.00")}])
    assert answer.result_type == "scalar"
    assert answer.answer.endswith("4000.00 USD.")
    assert answer.data == {"alias": "sum_amount", "value": Decimal("4000.00"), "currency": "USD"}


def test_several_currencies_are_listed_separately_never_added():
    answer = answer_for([{"currency": "USD", "sum_amount": Decimal("327000.00")},
                         {"currency": "INR", "sum_amount": Decimal("70800.00")}])
    assert answer.result_type == "tabular"
    assert "70800.00 INR; 327000.00 USD" in answer.answer
    assert "397800" not in answer.answer


def test_unknown_currency_is_shown_not_dropped():
    answer = answer_for([{"currency": None, "sum_amount": Decimal("10.00")},
                         {"currency": "USD", "sum_amount": Decimal("5.00")}])
    assert "5.00 USD; 10.00 (currency unknown)" in answer.answer



# Grouped money results (e.g. total per customer) are tables with a currency
# column; the answer text warns when the rows are not in one currency.

def grouped_answer(currencies):
    from result_analysis.models import ResultAnalysis
    rows = [{"customer": f"C{i}", "currency": c, "sum_amount": Decimal("1.00")}
            for i, c in enumerate(currencies)]
    result = ResultAnalysis(result_type="tabular", columns=["customer", "currency", "sum_amount"],
                            row_count=len(rows), currency_alias="currency", rows=rows)
    analysis = QuestionAnalysis(intent="aggregate", concepts=["Invoice"], measure_concept="Invoice",
                                measure_attribute="invoice amount", aggregation="SUM",
                                group_by_concepts=["Customer"])
    return AnswerService().build(analysis, result).answer


def test_grouped_rows_in_one_currency_need_no_warning():
    assert grouped_answer(["USD", "USD"]) == "Returned 2 matching rows."


def test_grouped_rows_in_several_currencies_are_flagged():
    answer = grouped_answer(["USD", "INR", "USD"])
    assert answer == ("Returned 3 matching rows. Amounts are in 2 currencies (INR, USD); "
                      "do not add rows in different currencies together.")


def test_grouped_rows_without_currency_are_flagged():
    assert "Some rows have no currency; do not add them to the USD amounts." in grouped_answer(["USD", None])
    answer = grouped_answer(["USD", "INR", ""])
    assert "2 currencies (INR, USD)" in answer and answer.endswith("Some rows have no currency.")


def test_grouped_rows_with_only_unknown_currency_add_nothing():
    # Every row already shows "(currency unknown)"; there is nothing to mix.
    assert grouped_answer([None, None]) == "Returned 2 matching rows."

# ----------------------------------------------------------
# Live, read-only
# ----------------------------------------------------------

def live_answer(response):
    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor

    neo4j = Neo4jClient()
    try:
        agent = build_research_agent(
            chat_model=MockChatModel(structured_response=response), neo4j_client=neo4j,
            postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100))
        return agent.ask("probe")
    finally:
        neo4j.close()


def question(measure, attribute, concepts, group=()):
    return {"intent": "aggregate", "concepts": concepts, "result_concept": concepts[0],
            "measure_concept": measure, "measure_attribute": attribute,
            "group_by_concepts": list(group), "entities": [], "aggregation": "SUM"}


@pytest.mark.integration
def test_live_total_invoice_amount_is_split_by_currency():
    answer = live_answer(question("Invoice", "invoice amount", ["Invoice"]))
    assert answer.result_type == "tabular"
    totals = {r["currency"]: Decimal(str(r["sum_amount"])) for r in answer.data}
    assert totals == {"USD": Decimal("327000.00"), "INR": Decimal("70800.00")}


@pytest.mark.integration
def test_live_single_currency_total_names_currency():
    answer = live_answer(question("PaymentReceipt", "payment amount", ["PaymentReceipt"]))
    assert answer.result_type == "scalar"
    assert answer.data["currency"] == "USD"
    assert Decimal(str(answer.data["value"])) == Decimal("72000.00")


@pytest.mark.integration
def test_live_grouped_totals_carry_currency_per_row():
    answer = live_answer(question("Invoice", "invoice amount", ["Customer", "Invoice"], ["Customer"]))
    rows = {r["customer_name"]: (r["currency"], Decimal(str(r["sum_amount"]))) for r in answer.data}
    assert rows["Bharat Industrial Systems"] == ("INR", Decimal("35400.00"))
    assert rows["Horizon India Enterprises"] == ("INR", Decimal("35400.00"))
    assert all(currency == "USD" for name, (currency, _) in rows.items()
               if name not in ("Bharat Industrial Systems", "Horizon India Enterprises"))
