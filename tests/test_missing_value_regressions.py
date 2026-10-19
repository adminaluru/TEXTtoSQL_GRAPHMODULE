"""
A3.5 / D-006: totals disclose records whose measured value is missing.

Before (Step 3 probe, 2026-10-15):

    "What is the total approved claim amount?"
    answered  "The total approved amount is 6750.00."
    data      9 of 18 claims have NULL approved_amount (pending)

SQL SUM ignores NULLs, so the answer silently covered half the claims.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from answering.answer_service import AnswerService
from nlu.models import QuestionAnalysis
from planning.missing_values import UNCOUNTABLE, MissingValuePolicy
from planning.models import QueryAggregation, QueryPlan
from result_analysis.analyzer import ResultAnalyzer
from sql_compiler.compiler import DeterministicSQLCompiler

CLAIMS = "public.claims"


def policy(keys=("id",)):
    ontology = Mock()
    ontology.get_table_columns.return_value = [SimpleNamespace(name=k, is_primary_key=True)
                                               for k in keys]
    return MissingValuePolicy(ontology)


def plan(function="SUM"):
    return QueryPlan(intent="aggregate", tables=[CLAIMS], aggregations=[
        QueryAggregation(function=function, table=CLAIMS, column="approved_amount", alias="v")])


@pytest.mark.parametrize("function", ["SUM", "AVG", "MIN", "MAX"])
def test_value_aggregates_count_missing_measure_records(function):
    result = policy().apply(plan(function))
    aggregation = result.aggregations[0]
    assert (aggregation.missing_key_column, aggregation.missing_alias) == ("id", "v_missing")
    sql = " ".join(DeterministicSQLCompiler().compile(result).sql.split())
    assert "COUNT(t0.id) FILTER (WHERE t0.approved_amount IS NULL) AS v_missing" in sql


def test_count_is_not_disclosed():
    original = plan("COUNT")
    assert policy().apply(original) == original


def test_table_without_key_gets_a_note_instead_of_a_wrong_count():
    result = policy(keys=()).apply(plan())
    assert result.aggregations[0].missing_alias is None
    assert result.result_notes == [UNCOUNTABLE]


def test_policy_is_idempotent():
    once = policy().apply(plan())
    assert policy().apply(once) == once


def answer(missing, measure_attribute="approved amount"):
    query = policy().apply(plan())
    execution = SimpleNamespace(rows=[{"v": Decimal("6750.00"), "v_missing": missing}], row_count=1,
                                truncated=False, columns=["v", "v_missing"])
    analyzer = ResultAnalyzer()
    analyzer._validate_execution_result = lambda e: None
    analysis = QuestionAnalysis(intent="aggregate", concepts=["InvoiceClaim"],
                                measure_concept="InvoiceClaim",
                                measure_attribute=measure_attribute, aggregation="SUM")
    return AnswerService().build(analysis, analyzer.analyze(execution, query))


def test_missing_records_are_stated():
    response = answer(9)
    assert response.result_type == "scalar"
    assert response.answer.endswith(
        "9 invoice claim records with no approved amount are not included.")
    assert response.data["missing_count"] == 9


def test_single_missing_record_reads_correctly():
    assert answer(1).answer.endswith(
        "1 invoice claim record with no approved amount is not included.")


def test_nothing_missing_adds_nothing():
    assert answer(0).answer == "The total approved amount is 6750.00."


@pytest.mark.integration
def test_live_approved_total_discloses_pending_claims():
    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor

    response = {"intent": "aggregate", "concepts": ["InvoiceClaim"], "result_concept": "InvoiceClaim",
                "measure_concept": "InvoiceClaim", "measure_attribute": "approved amount",
                "group_by_concepts": [], "entities": [], "aggregation": "SUM"}
    neo4j = Neo4jClient()
    try:
        agent = build_research_agent(chat_model=MockChatModel(structured_response=response),
                                     neo4j_client=neo4j,
                                     postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000,
                                                                          max_rows=100))
        result = agent.ask("What is the total approved claim amount?")
    finally:
        neo4j.close()
    assert Decimal(str(result.data["value"])) == Decimal("6750.00")
    assert result.data["currency"] == "USD"
    assert result.data["missing_count"] == 9
    assert "9 invoice claim records with no approved amount are not included." in result.answer
