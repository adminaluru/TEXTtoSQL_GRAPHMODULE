"""
A3.3: counts and totals qualified by related records use EXISTS.

Before (Step 3 probe, 2026-10-15) these were refused by FanOutGuard:

    "How many customers have claims?"           -> refused
    "Total amount of invoices that have claims" -> refused

The refusal was safe, but the questions are legitimate. Qualifying concepts
of the result records' own measure now become EXISTS conditions, which never
repeat outer rows. Answers state the condition.

Verified Golden Data (read-only SQL, 2026-10-15):

    customers with claims (EXISTS)          18 of 21
    invoice amount of invoices with claims  90000.00 USD
    LE-1001 payments via its invoices       4000.00 USD
"""

from decimal import Decimal
from unittest.mock import Mock

import pytest

from semantic.existence import existence_concepts


@pytest.mark.parametrize("intent,concepts,result,measure,filters,groups,expected", [
    # result records' own measure: extra concepts qualify them
    ("count", ["Customer", "Invoice", "InvoiceClaim"], "Customer", "Customer", set(), set(),
     ["Invoice", "InvoiceClaim"]),
    ("aggregate", ["Invoice", "InvoiceClaim"], "Invoice", "Invoice", set(), set(), ["InvoiceClaim"]),
    # measure on another concept: extra concepts may be path bridges, keep joins
    ("aggregate", ["Customer", "Invoice", "PaymentReceipt"], "Customer", "PaymentReceipt",
     {"Customer"}, set(), []),
    ("aggregate", ["Customer", "PaymentReceipt"], "Customer", "PaymentReceipt", {"Customer"}, set(), []),
    # filter and grouping concepts are never conditions
    ("count", ["Customer", "Invoice"], "Invoice", "Invoice", {"Customer"}, set(), []),
    ("count", ["Customer", "Invoice", "InvoiceClaim"], "Invoice", "Invoice", set(), {"Customer"},
     ["InvoiceClaim"]),
    # LIST behaviour unchanged
    ("list", ["Invoice", "BrokenPromiseToPay"], "Invoice", None, set(), set(), ["BrokenPromiseToPay"]),
    ("list", ["Customer", "Invoice"], "Invoice", None, {"Customer"}, set(), []),
])
def test_existence_rule(intent, concepts, result, measure, filters, groups, expected):
    assert existence_concepts(intent, concepts, result, measure, filters, groups) == expected


def live(response):
    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor

    neo4j = Neo4jClient()
    try:
        executor = PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100)
        executor.execute = Mock(wraps=executor.execute)
        agent = build_research_agent(chat_model=MockChatModel(structured_response=response),
                                     neo4j_client=neo4j, postgres_executor=executor)
        answer = agent.ask("probe")
        sql = [" ".join(c.kwargs["compiled"].sql.split()) for c in executor.execute.call_args_list]
        return answer, sql
    finally:
        neo4j.close()


def question(intent, concepts, result, measure, attribute=None, aggregation=None, entities=()):
    return {"intent": intent, "concepts": concepts, "result_concept": result,
            "measure_concept": measure, "measure_attribute": attribute, "group_by_concepts": [],
            "entities": list(entities), "aggregation": aggregation}


@pytest.mark.integration
def test_live_count_customers_with_claims_uses_exists():
    answer, sql = live(question("count", ["Customer", "Invoice", "InvoiceClaim"],
                                "Customer", "Customer", aggregation="COUNT"))
    assert answer.result_type == "scalar"
    assert answer.data["value"] == 18
    assert "with related invoice and invoice claim records" in answer.answer
    assert "WHERE EXISTS" in sql[-1]
    assert "FROM public.erp_customer_details AS t0 WHERE EXISTS" in sql[-1]


@pytest.mark.integration
def test_live_invoice_total_qualified_by_claims_uses_exists():
    answer, sql = live(question("aggregate", ["Invoice", "InvoiceClaim"], "Invoice", "Invoice",
                                "invoice amount", "SUM"))
    assert answer.result_type == "scalar"
    assert Decimal(str(answer.data["value"])) == Decimal("90000.00")
    assert answer.data["currency"] == "USD"
    assert "with related invoice claim records" in answer.answer
    assert "WHERE EXISTS" in sql[-1] and " JOIN public.d_claim_invoice" not in sql[-1].split("EXISTS")[0]


@pytest.mark.integration
def test_live_measure_path_bridge_stays_a_join():
    answer, sql = live(question(
        "aggregate", ["Customer", "Invoice", "PaymentReceipt"], "Customer", "PaymentReceipt",
        "payment amount", "SUM",
        [{"concept": "Customer", "attribute": "customer name",
          "value": "Blue Prism Technologies LE-1001"}]))
    assert Decimal(str(answer.data["value"])) == Decimal("4000.00")
    assert "EXISTS" not in sql[-1]
    assert "with related" not in answer.answer
