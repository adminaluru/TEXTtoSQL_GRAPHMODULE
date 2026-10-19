"""
Date periods (decision D-007, 2026-10-15).

- US Central time (America/Chicago); stored timestamps are already local,
  so only "today" is time-zone dependent.
- End dates are included: start <= value < day after end.
- Unclear periods are refused with the supported forms, never guessed.

Verified Golden Data (read-only SQL, 2026-10-15):

    LE-1001 payments   2026-04-20 15:00  3000.00
                       2026-05-20 15:00  1000.00
    all payments on 2026-05-20           18 rows, 18000.00
    2026-04-21 to 2026-05-20 inclusive   18000.00 (end excluded would be none)
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from planning.date_period import Period, business_today, parse_period
from planning.errors import UnsupportedPredicateError

TODAY = date(2026, 10, 15)


@pytest.mark.parametrize("text,start,end", [
    ("2026-04-20", date(2026, 4, 20), date(2026, 4, 20)),
    ("20 April 2026", date(2026, 4, 20), date(2026, 4, 20)),
    ("April 20, 2026", date(2026, 4, 20), date(2026, 4, 20)),
    ("on 2026-04-20", date(2026, 4, 20), date(2026, 4, 20)),
    ("April 2026", date(2026, 4, 1), date(2026, 4, 30)),
    ("2026-02", date(2026, 2, 1), date(2026, 2, 28)),
    ("2028-02", date(2028, 2, 1), date(2028, 2, 29)),
    ("2026", date(2026, 1, 1), date(2026, 12, 31)),
    ("in 2026", date(2026, 1, 1), date(2026, 12, 31)),
    ("Q2 2026", date(2026, 4, 1), date(2026, 6, 30)),
    ("2026 Q4", date(2026, 10, 1), date(2026, 12, 31)),
    ("2026-04-01 to 2026-04-30", date(2026, 4, 1), date(2026, 4, 30)),
    ("from 2026-04-01 until 2026-04-15", date(2026, 4, 1), date(2026, 4, 15)),
    ("between April 2026 and May 2026", date(2026, 4, 1), date(2026, 5, 31)),
    ("2026-04-01 - 2026-04-30", date(2026, 4, 1), date(2026, 4, 30)),
    ("before 2026-05-01", None, date(2026, 4, 30)),
    ("after April 2026", date(2026, 5, 1), None),
    ("since 2026-04-20", date(2026, 4, 20), None),
    ("until 2026-04-20", None, date(2026, 4, 20)),
    ("today", TODAY, TODAY),
    ("yesterday", date(2026, 10, 14), date(2026, 10, 14)),
    ("this month", date(2026, 10, 1), date(2026, 10, 31)),
    ("last month", date(2026, 9, 1), date(2026, 9, 30)),
    ("this quarter", date(2026, 10, 1), date(2026, 12, 31)),
    ("last quarter", date(2026, 7, 1), date(2026, 9, 30)),
    ("this year", date(2026, 1, 1), date(2026, 12, 31)),
    ("last year", date(2025, 1, 1), date(2025, 12, 31)),
])
def test_supported_periods(text, start, end):
    assert parse_period(text, TODAY) == Period(start, end)


@pytest.mark.parametrize("today,text,start,end", [
    (date(2026, 1, 10), "last month", date(2025, 12, 1), date(2025, 12, 31)),
    (date(2026, 2, 10), "last quarter", date(2025, 10, 1), date(2025, 12, 31)),
])
def test_relative_periods_cross_year_boundaries(today, text, start, end):
    assert parse_period(text, today) == Period(start, end)


def test_end_date_is_included_in_full():
    period = parse_period("2026-04-01 to 2026-04-30", TODAY)
    assert period.start_at == datetime(2026, 4, 1)
    assert period.end_before == datetime(2026, 5, 1)       # whole of 30 April


@pytest.mark.parametrize("text", [
    "April",               # which year?
    "last week",           # Monday or Sunday start?
    "last 30 days",        # is today included?
    "recently", "soon", "Q5 2026", "2026-02-30", "Smarch 2026", "",
])
def test_unclear_periods_are_refused_with_supported_forms(text):
    with pytest.raises(UnsupportedPredicateError, match="Please use a specific day"):
        parse_period(text, TODAY)


def test_reversed_range_is_refused():
    with pytest.raises(UnsupportedPredicateError, match="ends before it starts"):
        parse_period("2026-05-01 to 2026-04-01", TODAY)


@pytest.mark.parametrize("utc_moment,expected", [
    (datetime(2026, 10, 16, 3, 30, tzinfo=timezone.utc), date(2026, 10, 15)),  # 22:30 CDT
    (datetime(2026, 10, 16, 5, 30, tzinfo=timezone.utc), date(2026, 10, 16)),  # 00:30 CDT
    (datetime(2026, 1, 16, 5, 30, tzinfo=timezone.utc), date(2026, 1, 15)),    # 23:30 CST
])
def test_today_is_the_central_time_date(utc_moment, expected):
    assert business_today(utc_moment) == expected


def test_describe_states_the_rule():
    assert parse_period("April 2026", TODAY).describe() == (
        "2026-04-01 to 2026-04-30 (US Central time, end date included)")


# ----------------------------------------------------------
# Live, read-only
# ----------------------------------------------------------

def live_total(period, name=None):
    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor

    entities = [{"concept": "PaymentReceipt", "attribute": "payment date", "value": period}]
    concepts = ["PaymentReceipt"]
    if name:
        entities.insert(0, {"concept": "Customer", "attribute": "customer name", "value": name})
        concepts = ["Customer", "PaymentReceipt"]
    response = {"intent": "aggregate", "concepts": concepts, "result_concept": concepts[0],
                "measure_concept": "PaymentReceipt", "measure_attribute": "payment amount",
                "group_by_concepts": [], "entities": entities, "aggregation": "SUM"}
    neo4j = Neo4jClient()
    try:
        agent = build_research_agent(chat_model=MockChatModel(structured_response=response),
                                     neo4j_client=neo4j,
                                     postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000,
                                                                          max_rows=100))
        return agent.ask("How much was paid?")
    finally:
        neo4j.close()


@pytest.mark.integration
@pytest.mark.parametrize("period,expected", [
    ("2026-04-20", "3000.00"),
    ("2026-05-20", "1000.00"),
    ("April 2026", "3000.00"),
    ("2026", "4000.00"),
    ("before 2026-05-20", "3000.00"),
    ("since 2026-05-20", "1000.00"),
])
def test_live_customer_payments_by_period(period, expected):
    answer = live_total(period, name="Blue Prism Technologies LE-1001")
    assert Decimal(str(answer.data["value"])) == Decimal(expected)
    assert answer.data["currency"] == "USD"
    assert any("US Central time, end date included" in w for w in answer.warnings)


@pytest.mark.integration
def test_live_range_includes_the_whole_end_day():
    # Every payment on 2026-05-20 is at 15:00; excluding the end day would
    # return nothing.
    answer = live_total("2026-04-21 to 2026-05-20")
    assert Decimal(str(answer.data["value"])) == Decimal("18000.00")


@pytest.mark.integration
def test_live_period_with_no_payments_is_empty_not_zero():
    answer = live_total("2025")
    assert answer.result_type == "empty"
