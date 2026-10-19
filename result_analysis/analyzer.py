from execution.models import (
    PostgreSQLExecutionResult,
)

from planning.models import (
    QueryPlan,
)

from result_analysis.models import (
    ResultAnalysis,
)


class ResultAnalyzer:
    """
    Convert raw PostgreSQL execution output into a stable
    internal result representation.

    Responsibilities:

    - recognize empty results
    - recognize scalar aggregate results
    - preserve tabular results
    - validate execution-result consistency
    - surface truncation warnings

    Does NOT:

    - generate natural-language answers
    - call an LLM
    - call Neo4j
    - call PostgreSQL
    - modify SQL
    - perform schema resolution
    """

    def analyze(
        self,
        execution: PostgreSQLExecutionResult,
        plan: QueryPlan,
    ) -> ResultAnalysis:

        self._validate_execution_result(
            execution
        )

        warnings: list[str] = list(
            plan.result_notes
        )

        if execution.truncated:

            warnings.append(
                "Database result was truncated "
                "by the execution row limit."
            )

        # ==================================================
        # 1. No rows
        # ==================================================

        if execution.row_count == 0:

            return ResultAnalysis(
                result_type="empty",
                columns=execution.columns,
                row_count=0,
                truncated=execution.truncated,
                scalar_alias=None,
                scalar_value=None,
                rows=[],
                warnings=warnings,
            )

        # ==================================================
        # 2. Scalar aggregate
        #
        # A result is scalar only when the physical plan
        # itself is scalar:
        #
        # - exactly one aggregation
        # - exactly one returned row
        # - no non-aggregate projections
        # - no GROUP BY columns
        #
        # This distinction matters because a grouped
        # aggregate can legitimately return one row while
        # still being tabular.
        #
        # Current scalar aggregate vertical slice:
        #
        # SELECT SUM(...) AS sum_payment_amount
        #
        # PostgreSQL returns:
        #
        # {
        #     "sum_payment_amount": ...
        # }
        # ==================================================

        # A currency split (D-004) adds one currency projection and
        # grouping; one currency row is still one logical value.
        currency_alias = plan.currency_alias

        other_projections = [
            p for p in plan.projections
            if p.alias != currency_alias
        ]

        if (
            len(plan.aggregations) == 1
            and execution.row_count == 1
            and not other_projections
            and len(plan.groupings)
            == (1 if currency_alias else 0)
        ):

            aggregation = (
                plan.aggregations[0]
            )

            alias = aggregation.alias

            row = execution.rows[0]

            if alias not in row:

                raise ValueError(
                    "PostgreSQL result does not contain "
                    "the aggregation alias expected by "
                    f"QueryPlan: {alias}"
                )

            return ResultAnalysis(
                result_type="scalar",

                columns=execution.columns,

                row_count=1,

                truncated=execution.truncated,

                scalar_alias=alias,

                scalar_value=row[alias],

                currency_alias=currency_alias,

                missing_alias=aggregation.missing_alias,

                missing_count=(
                    row.get(aggregation.missing_alias)
                    if aggregation.missing_alias
                    else None
                ),

                scalar_currency=(
                    row.get(currency_alias)
                    if currency_alias
                    else None
                ),

                rows=execution.rows,

                warnings=warnings,
            )

        # ==================================================
        # 3. Tabular result
        #
        # Future examples:
        #
        # customer | total_payment
        # ---------|--------------
        # A        | 100
        # B        | 200
        #
        # or:
        #
        # invoice_id | status | amount
        # ==================================================

        return ResultAnalysis(
            result_type="tabular",

            currency_alias=plan.currency_alias,

            missing_alias=(
                plan.aggregations[0].missing_alias
                if len(plan.aggregations) == 1
                else None
            ),

            missing_count=self._sum_missing(
                plan,
                execution.rows,
            ),

            columns=execution.columns,

            row_count=execution.row_count,

            truncated=execution.truncated,

            scalar_alias=None,

            scalar_value=None,

            rows=execution.rows,

            warnings=warnings,
        )

    # ======================================================
    # Internal validation
    # ======================================================

    @staticmethod
    def _sum_missing(
        plan: QueryPlan,
        rows: list[dict],
    ) -> int | None:

        aliases = [
            a.missing_alias
            for a in plan.aggregations
            if a.missing_alias
        ]

        if not aliases:
            return None

        return sum(
            row.get(alias) or 0
            for row in rows
            for alias in aliases
        )

    @staticmethod
    def _validate_execution_result(
        execution: PostgreSQLExecutionResult,
    ) -> None:

        if execution.row_count < 0:

            raise ValueError(
                "Execution result row_count "
                "cannot be negative."
            )

        if (
            execution.row_count
            != len(execution.rows)
        ):

            raise ValueError(
                "Execution result row_count does not "
                "match the number of returned rows."
            )

        if not execution.rows:
            return

        expected_columns = set(
            execution.columns
        )

        for index, row in enumerate(
            execution.rows
        ):

            actual_columns = set(
                row.keys()
            )

            if actual_columns != expected_columns:

                raise ValueError(
                    "Execution result row columns do not "
                    "match execution metadata. "
                    f"Row {index}: "
                    f"expected={sorted(expected_columns)}, "
                    f"actual={sorted(actual_columns)}"
                )