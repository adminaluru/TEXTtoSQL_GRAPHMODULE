"""
A3.1: a date/time condition must never become an exact-equality filter.

Defect found by the Step 3 probe (2026-10-15):

    "How much has Blue Prism Technologies LE-1001 paid on 2026-04-20?"
    compiled to  payment_date = '2026-04-20'
    answered     "No aggregate value was available"
    truth        3000.00 (read-only SQL with a day range)

payment_date is a timestamp and no payment is at exactly midnight, so the
equality silently matched nothing. _resolve_filter_match still refuses a
temporal column (defence in depth); since D-007 the resolver turns date
conditions into exact periods first (see test_date_period_regressions.py),
and unclear ones are refused, never repaired by dropping them.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai.providers.mock_chat import MockChatModel
from nlu.question_analyzer import QuestionAnalyzer
from planning.column_resolver import ColumnResolver
from planning.errors import UnsupportedPredicateError


def column(data_type, category, name="payment_date"):
    return SimpleNamespace(name=name, data_type=data_type, category=category)


@pytest.mark.parametrize("data_type,category", [
    ("DATETIME", "timestamp"),
    ("TIMESTAMP", "timestamp"),
    ("DATE", None),
    ("TIMESTAMP WITH TIME ZONE", None),
    ("VARCHAR(50)", "timestamp"),      # metadata category alone is enough
])
@pytest.mark.parametrize("value", ["2026-04-20", "2026", "April 2026", 2026])
def test_temporal_filter_is_refused(data_type, category, value):
    with pytest.raises(UnsupportedPredicateError, match="[Dd]ate"):
        ColumnResolver._resolve_filter_match(
            business_attribute="payment date", column=column(data_type, category), value=value)


@pytest.mark.parametrize("data_type,category,value,expected", [
    ("VARCHAR(50)", "dimension", "OPEN", ("=", "OPEN")),
    ("BIGINT", "identifier", 527, ("=", 527)),
])
def test_non_temporal_filters_are_unchanged(data_type, category, value, expected):
    assert ColumnResolver._resolve_filter_match(
        business_attribute="status", column=column(data_type, category, "inv_status"),
        value=value) == expected


def test_unsupported_predicate_is_a_value_error_with_safe_text():
    error = UnsupportedPredicateError("Date and time conditions are not supported yet.")
    assert isinstance(error, ValueError)
    assert "payment_date" not in str(error)


def test_orchestrator_refuses_without_repair_or_execution():
    from agent.orchestrator import ResearchAgentOrchestrator
    agent = ResearchAgentOrchestrator.__new__(ResearchAgentOrchestrator)
    agent._max_repair_attempts = 2
    agent._repair_budget_seconds = 30.0
    agent._interpret = Mock(side_effect=UnsupportedPredicateError(
        "Date and time conditions are not supported yet."))
    agent._execute = Mock()
    response = agent.ask("How much was paid on 2026-04-20?")
    assert response.result_type == "clarification"
    assert "Date and time conditions are not supported yet." in response.answer
    assert agent._interpret.call_count == 1
    agent._execute.assert_not_called()


def test_prompt_forbids_omitting_conditions():
    analyzer = QuestionAnalyzer.__new__(QuestionAnalyzer)
    prompt = analyzer._build_system_prompt([])
    assert "Never omit a condition" in prompt
    assert "date" in prompt.lower()


@pytest.mark.integration
@pytest.mark.parametrize("value,expected", [
    ("2026-04-20", "3000.00"),      # D-007: day period, not instant equality
    ("recently", None),             # unclear period: refused, not dropped
])
def test_live_date_question_never_answers_from_equality(value, expected):
    from decimal import Decimal

    from agent.factory import build_research_agent
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor

    response = {
        "intent": "aggregate",
        "concepts": ["Customer", "PaymentReceipt"],
        "result_concept": "Customer",
        "measure_concept": "PaymentReceipt",
        "measure_attribute": "payment amount",
        "group_by_concepts": [],
        "entities": [
            {"concept": "Customer", "attribute": "customer name",
             "value": "Blue Prism Technologies LE-1001"},
            {"concept": "PaymentReceipt", "attribute": "payment date", "value": value},
        ],
        "aggregation": "SUM",
    }
    neo4j = Neo4jClient()
    try:
        executor = PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100)
        executor.execute = Mock(wraps=executor.execute)
        agent = build_research_agent(chat_model=MockChatModel(structured_response=response),
                                     neo4j_client=neo4j, postgres_executor=executor)
        answer = agent.ask(f"How much has Blue Prism Technologies LE-1001 paid {value}?")
        executed = [" ".join(c.kwargs["compiled"].sql.split()) for c in executor.execute.call_args_list]
    finally:
        neo4j.close()
    assert not any("payment_date = " in sql for sql in executed)
    if expected is None:
        assert answer.result_type == "clarification"
        assert "can't tell which dates" in answer.answer
        executor.execute.assert_not_called()
    else:
        assert Decimal(str(answer.data["value"])) == Decimal(expected)
