from decimal import Decimal

from execution.models import (
    PostgreSQLExecutionResult,
)

from planning.models import (
    QueryAggregation,
    QueryGrouping,
    QueryPlan,
    QueryProjection,
)

from result_analysis.analyzer import (
    ResultAnalyzer,
)


def test_result_analyzer_scalar_aggregate():

    plan = QueryPlan(
        intent="aggregate",

        grain=[
            "public.erp_customer_details"
        ],

        tables=[
            "public.erp_customer_details",
            "public.erp_payment_receipt",
        ],

        joins=[],

        filters=[],

        projections=[],

        aggregations=[
            QueryAggregation(
                function="SUM",

                table=(
                    "public.erp_payment_receipt"
                ),

                column="payment_amount",

                alias="sum_payment_amount",
            )
        ],

        groupings=[],

        limit=None,
    )

    execution = PostgreSQLExecutionResult(
        columns=[
            "sum_payment_amount"
        ],

        rows=[
            {
                "sum_payment_amount":
                    Decimal("12500.00")
            }
        ],

        row_count=1,

        truncated=False,

        execution_ms=5.4,
    )

    result = ResultAnalyzer().analyze(
        execution=execution,
        plan=plan,
    )

    assert result.result_type == "scalar"

    assert (
        result.scalar_alias
        == "sum_payment_amount"
    )

    assert (
        result.scalar_value
        == Decimal("12500.00")
    )

    assert result.row_count == 1

    assert result.truncated is False

    assert result.warnings == []


def test_result_analyzer_empty_result():

    plan = QueryPlan(
        intent="lookup",

        grain=[
            "public.erp_customer_details"
        ],

        tables=[
            "public.erp_customer_details"
        ],

        joins=[],

        filters=[],

        projections=[],

        aggregations=[],

        groupings=[],

        limit=100,
    )

    execution = PostgreSQLExecutionResult(
        columns=[
            "id",
            "name",
        ],

        rows=[],

        row_count=0,

        truncated=False,

        execution_ms=2.0,
    )

    result = ResultAnalyzer().analyze(
        execution=execution,
        plan=plan,
    )

    assert result.result_type == "empty"

    assert result.row_count == 0

    assert result.scalar_value is None


def test_result_analyzer_tabular_result():

    plan = QueryPlan(
        intent="list",

        grain=[
            "public.erp_customer_details"
        ],

        tables=[
            "public.erp_customer_details"
        ],

        joins=[],

        filters=[],

        projections=[],

        aggregations=[],

        groupings=[],

        limit=100,
    )

    execution = PostgreSQLExecutionResult(
        columns=[
            "id",
            "name",
        ],

        rows=[
            {
                "id": 1,
                "name": "Customer A",
            },
            {
                "id": 2,
                "name": "Customer B",
            },
        ],

        row_count=2,

        truncated=False,

        execution_ms=3.1,
    )

    result = ResultAnalyzer().analyze(
        execution=execution,
        plan=plan,
    )

    assert result.result_type == "tabular"

    assert result.row_count == 2

    assert len(result.rows) == 2

    assert result.scalar_value is None


# ==========================================================
# Regression:
# single-row GROUP BY result must remain TABULAR
# ==========================================================

def test_result_analyzer_single_row_grouped_aggregate_remains_tabular():
    """
    A grouped aggregate can legitimately return only
    one group.

    Example:

        SELECT
            customer.name,
            SUM(payment.amount)
        ...
        GROUP BY
            customer.id,
            customer.name

    Even if PostgreSQL returns only one row, this is
    still a grouped/tabular result, not a scalar.
    """

    plan = QueryPlan(
        intent="aggregate",

        grain=[
            "public.erp_customer_details"
        ],

        tables=[
            "public.erp_customer_details",
            "public.erp_payment_receipt",
        ],

        joins=[],

        filters=[],

        projections=[
            QueryProjection(
                table=
                    "public.erp_customer_details",

                column=
                    "name",

                alias=
                    "customer_name",
            )
        ],

        aggregations=[
            QueryAggregation(
                function=
                    "SUM",

                table=
                    "public.erp_payment_receipt",

                column=
                    "payment_amount",

                alias=
                    "sum_payment_amount",
            )
        ],

        groupings=[
            QueryGrouping(
                table=
                    "public.erp_customer_details",

                column=
                    "id",
            ),

            QueryGrouping(
                table=
                    "public.erp_customer_details",

                column=
                    "name",
            ),
        ],

        limit=None,
    )

    execution = PostgreSQLExecutionResult(
        columns=[
            "customer_name",
            "sum_payment_amount",
        ],

        rows=[
            {
                "customer_name":
                    "Vertex Global Services LE-1004",

                "sum_payment_amount":
                    Decimal("4000.00"),
            }
        ],

        row_count=1,

        truncated=False,

        execution_ms=3.5,
    )

    result = ResultAnalyzer().analyze(
        execution=execution,
        plan=plan,
    )

    # ------------------------------------------------------
    # Critical regression check:
    #
    # 1 aggregate + 1 row does NOT imply scalar
    # when GROUP BY/projection semantics exist.
    # ------------------------------------------------------

    assert (
        result.result_type
        == "tabular"
    )

    assert (
        result.row_count
        == 1
    )

    assert (
        result.scalar_alias
        is None
    )

    assert (
        result.scalar_value
        is None
    )

    assert (
        len(result.rows)
        == 1
    )

    assert (
        result.rows[0]["customer_name"]
        ==
        "Vertex Global Services LE-1004"
    )

    assert (
        result.rows[0][
            "sum_payment_amount"
        ]
        ==
        Decimal("4000.00")
    )

    assert (
        result.warnings
        == []
    )