"""
A3.4 / D-005: groups with no matching records are listed, not dropped.

Before (Step 3 probe, 2026-10-15): "total payments by customer" returned 18
rows; Bharat Industrial Systems, Global Tech Imports LLC and Horizon India
Enterprises (no payments) silently disappeared through an INNER JOIN.
"""

import pytest

from planning.empty_groups import EMPTY_GROUPS_OMITTED, EmptyGroupPolicy
from planning.models import (QueryAggregation, QueryExistence, QueryFilter, QueryGrouping,
                             QueryJoin, QueryPlan, QueryProjection)
from sql_compiler.compiler import DeterministicSQLCompiler

C, P = "public.customers", "public.payments"


def grouped(function="SUM", filters=(), tables=(C, P), currency=False, existence=()):
    plan = QueryPlan(
        intent="aggregate", tables=list(tables),
        joins=[QueryJoin(left_table=P, left_column="customer_id", right_table=C, right_column="id",
                         validation_status="confirmed")],
        projections=[QueryProjection(table=C, column="name", alias="customer_name")],
        groupings=[QueryGrouping(table=C, column="id"), QueryGrouping(table=C, column="name")],
        aggregations=[QueryAggregation(function=function, table=P, column="amount", alias="v")],
        filters=list(filters), existence_conditions=list(existence))
    if currency:
        plan.projections.append(QueryProjection(table=P, column="cur", alias="currency"))
        plan.groupings.append(QueryGrouping(table=P, column="cur"))
        plan.currency_alias = "currency"
    return plan


@pytest.mark.parametrize("function,zero", [("SUM", True), ("COUNT", True), ("AVG", False),
                                           ("MIN", False), ("MAX", False)])
def test_safe_grouping_keeps_empty_groups(function, zero):
    plan = EmptyGroupPolicy().apply(grouped(function))
    assert [j.join_type for j in plan.joins] == ["LEFT"]
    assert plan.aggregations[0].empty_as_zero is zero
    assert plan.result_notes == []
    sql = DeterministicSQLCompiler().compile(plan).sql
    assert "LEFT JOIN" in sql
    assert ("COALESCE(" in sql) is zero


def test_currency_grouping_does_not_block_the_rule():
    plan = EmptyGroupPolicy().apply(grouped(currency=True))
    assert [j.join_type for j in plan.joins] == ["LEFT"]


@pytest.mark.parametrize("plan", [
    grouped(filters=[QueryFilter(table=P, column="status", operator="=", value="X")]),
    grouped(tables=(P, C)),
    grouped(existence=[QueryExistence(anchor_table=P, tables=["public.other"])]),
])
def test_unsafe_grouping_is_unchanged_and_says_so(plan):
    result = EmptyGroupPolicy().apply(plan)
    assert [j.join_type for j in result.joins] == ["INNER"]
    assert not result.aggregations[0].empty_as_zero
    assert result.result_notes == [EMPTY_GROUPS_OMITTED]


def test_filter_on_grouped_table_is_safe():
    plan = EmptyGroupPolicy().apply(
        grouped(filters=[QueryFilter(table=C, column="company_id", operator="=", value=527)]))
    assert plan.joins[0].join_type == "LEFT" and not plan.result_notes


@pytest.mark.parametrize("plan", [
    QueryPlan(intent="aggregate", tables=[P],
              aggregations=[QueryAggregation(function="SUM", table=P, column="amount", alias="v")]),
    QueryPlan(intent="aggregate", tables=[P], currency_alias="currency",
              projections=[QueryProjection(table=P, column="cur", alias="currency")],
              groupings=[QueryGrouping(table=P, column="cur")],
              aggregations=[QueryAggregation(function="SUM", table=P, column="amount", alias="v")]),
])
def test_scalar_totals_are_untouched(plan):
    assert EmptyGroupPolicy().apply(plan) == plan


@pytest.mark.parametrize("function", ["AVG", "MIN", "MAX"])
def test_compiler_never_turns_missing_average_or_extreme_into_zero(function):
    plan = grouped(function)
    plan.aggregations[0].empty_as_zero = True
    with pytest.raises(ValueError, match="only valid for SUM and COUNT"):
        DeterministicSQLCompiler().compile(plan)


def test_omitted_note_reaches_the_answer():
    from types import SimpleNamespace
    from result_analysis.analyzer import ResultAnalyzer
    plan = EmptyGroupPolicy().apply(grouped(tables=(P, C)))
    execution = SimpleNamespace(rows=[{"customer_name": "A", "v": 1}], row_count=1,
                                truncated=False, columns=["customer_name", "v"])
    analyzer = ResultAnalyzer()
    analyzer._validate_execution_result = lambda e: None
    assert EMPTY_GROUPS_OMITTED in analyzer.analyze(execution, plan).warnings
