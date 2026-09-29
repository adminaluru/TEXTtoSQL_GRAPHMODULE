import re

from planning.models import QueryPlan

from sql_compiler.models import CompiledQuery

from sql_validator.models import (
    SQLValidationResult,
)


class SQLValidator:
    """
    Validate compiled SQL before database execution.

    This component performs NO business reasoning and
    does NOT modify SQL.

    Responsibilities:

    - enforce read-only SELECT queries
    - reject multiple statements
    - reject dangerous SQL keywords
    - ensure parameter count matches placeholders
    - ensure only QueryPlan-authorized tables appear
    - include bounded existence-branch tables in that scope
    - ensure all authorized tables actually appear
    - reject SQL comments
    - reject SELECT *
    - validate grouped aggregate structure
    - validate projection / GROUP BY consistency
    """

    FORBIDDEN_KEYWORDS = {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
        "MERGE",
        "CALL",
        "COPY",
        "VACUUM",
        "ANALYZE",
        "COMMENT",
        "DO",
    }

    TABLE_PATTERN = re.compile(
        r"""
        \b(?:FROM|JOIN)\s+
        ([A-Za-z_][A-Za-z0-9_]*\.
         [A-Za-z_][A-Za-z0-9_]*)
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    WORD_PATTERN = re.compile(
        r"\b[A-Za-z_]+\b"
    )

    # ======================================================
    # Public API
    # ======================================================

    def validate(
        self,
        compiled: CompiledQuery,
        plan: QueryPlan,
    ) -> SQLValidationResult:

        errors: list[str] = []
        warnings: list[str] = []

        sql = compiled.sql.strip()

        # --------------------------------------------------
        # Empty SQL
        # --------------------------------------------------

        if not sql:
            errors.append(
                "Compiled SQL is empty."
            )

            return SQLValidationResult(
                valid=False,
                errors=errors,
                warnings=warnings,
            )

        # --------------------------------------------------
        # Must begin with SELECT
        # --------------------------------------------------

        if not re.match(
            r"^\s*SELECT\b",
            sql,
            re.IGNORECASE,
        ):
            errors.append(
                "Only SELECT queries are allowed."
            )

        # --------------------------------------------------
        # Multiple statements / semicolon
        #
        # Compiler currently emits no semicolon.
        # Reject any semicolon rather than trying to parse
        # statement boundaries.
        # --------------------------------------------------

        if ";" in sql:
            errors.append(
                "Semicolons are not allowed in compiled SQL."
            )

        # --------------------------------------------------
        # SQL comments
        #
        # There is no reason for our deterministic compiler
        # to generate comments.
        # --------------------------------------------------

        if "--" in sql:
            errors.append(
                "SQL line comments are not allowed."
            )

        if "/*" in sql or "*/" in sql:
            errors.append(
                "SQL block comments are not allowed."
            )

        # --------------------------------------------------
        # Forbidden operations
        # --------------------------------------------------

        words = {
            word.upper()
            for word in self.WORD_PATTERN.findall(
                sql
            )
        }

        forbidden_found = sorted(
            words
            & self.FORBIDDEN_KEYWORDS
        )

        if forbidden_found:
            errors.append(
                "Forbidden SQL keyword(s): "
                + ", ".join(
                    forbidden_found
                )
            )

        # --------------------------------------------------
        # Reject SELECT *
        # --------------------------------------------------

        if re.search(
            r"\bSELECT\s+\*",
            sql,
            re.IGNORECASE,
        ):
            errors.append(
                "SELECT * is not allowed."
            )

        if re.search(
            r"\b[A-Za-z_][A-Za-z0-9_]*\.\*",
            sql,
            re.IGNORECASE,
        ):
            errors.append(
                "Wildcard table projections are not allowed."
            )

        # --------------------------------------------------
        # Parameter count
        # --------------------------------------------------

        placeholder_count = sql.count(
            "%s"
        )

        parameter_count = len(
            compiled.parameters
        )

        if placeholder_count != parameter_count:
            errors.append(
                "SQL placeholder count does not match "
                "parameter count: "
                f"{placeholder_count} placeholders vs "
                f"{parameter_count} parameters."
            )

        # --------------------------------------------------
        # Table scope
        # --------------------------------------------------

        sql_tables = self._extract_tables(
            sql
        )

        planned_tables = set(
            plan.tables
        )

        existence_tables = {
            table

            for condition
            in plan.existence_conditions

            for table
            in condition.tables
        }

        authorized_tables = (
            planned_tables
            | existence_tables
        )

        unexpected_tables = (
            sql_tables
            - authorized_tables
        )

        if unexpected_tables:
            errors.append(
                "SQL references table(s) not present "
                "in QueryPlan: "
                + ", ".join(
                    sorted(
                        unexpected_tables
                    )
                )
            )

        missing_tables = (
            authorized_tables
            - sql_tables
        )

        if missing_tables:
            errors.append(
                "QueryPlan-authorized table(s) missing from SQL: "
                + ", ".join(
                    sorted(
                        missing_tables
                    )
                )
            )

        # --------------------------------------------------
        # Plan must itself contain tables
        # --------------------------------------------------

        if not plan.tables:
            errors.append(
                "QueryPlan contains no tables."
            )

        # --------------------------------------------------
        # Existence-branch structural validation
        # --------------------------------------------------

        for condition in plan.existence_conditions:

            if (
                condition.anchor_table
                not in planned_tables
            ):

                errors.append(
                    "Existence anchor table is not part "
                    "of the outer QueryPlan: "
                    f"{condition.anchor_table}"
                )

            if (
                not condition.tables
                or condition.tables[0]
                != condition.anchor_table
            ):

                errors.append(
                    "Existence condition must begin with "
                    "its declared anchor table."
                )

            if len(condition.tables) < 2:

                errors.append(
                    "Existence condition requires at least "
                    "one related table."
                )

            for join in condition.joins:

                if (
                    join.left_table
                    not in condition.tables
                    or join.right_table
                    not in condition.tables
                ):

                    errors.append(
                        "Existence join references a table "
                        "outside its branch: "
                        f"{join.left_table} -> "
                        f"{join.right_table}"
                    )

        # --------------------------------------------------
        # Join safety
        # --------------------------------------------------

        all_joins = list(
            plan.joins
        )

        for condition in plan.existence_conditions:

            all_joins.extend(
                condition.joins
            )

        for join in all_joins:

            if (
                join.validation_status
                and join.validation_status
                not in {
                    "confirmed",
                    "composite_join_recommended",
                }
            ):
                errors.append(
                    "QueryPlan contains unsafe join "
                    "validation status: "
                    f"{join.validation_status}"
                )

        # --------------------------------------------------
        # Aggregate-plan consistency
        # --------------------------------------------------

        if (
            plan.intent == "aggregate"
            and not plan.aggregations
        ):
            errors.append(
                "Aggregate QueryPlan contains no "
                "aggregation definition."
            )

        # ==================================================
        # Projection / GROUP BY consistency
        # ==================================================

        # --------------------------------------------------
        # Projection tables must belong to QueryPlan
        # --------------------------------------------------

        for projection in (
            plan.projections
        ):

            if (
                projection.table
                not in planned_tables
            ):

                errors.append(
                    "Projection references table "
                    "outside QueryPlan: "
                    f"{projection.table}"
                )

        # --------------------------------------------------
        # Grouping tables must belong to QueryPlan
        # --------------------------------------------------

        for grouping in (
            plan.groupings
        ):

            if (
                grouping.table
                not in planned_tables
            ):

                errors.append(
                    "Grouping references table "
                    "outside QueryPlan: "
                    f"{grouping.table}"
                )

        # --------------------------------------------------
        # Grouped aggregate structural consistency
        # --------------------------------------------------

        if (
            plan.intent
            in {
                "aggregate",
                "count",
            }
        ):

            # ----------------------------------------------
            # Projection in an aggregate query means the
            # query must be grouped.
            # ----------------------------------------------

            if (
                plan.projections
                and not plan.groupings
            ):

                errors.append(
                    "Aggregate QueryPlan contains "
                    "non-aggregate projections but "
                    "no GROUP BY definitions."
                )

            # ----------------------------------------------
            # GROUP BY without a display projection is
            # suspicious for our current grouped-aggregate
            # execution contract.
            # ----------------------------------------------

            if (
                plan.groupings
                and not plan.projections
            ):

                errors.append(
                    "Grouped aggregate QueryPlan "
                    "contains GROUP BY definitions "
                    "but no display projections."
                )

        # --------------------------------------------------
        # Every non-aggregate projected column must
        # participate in GROUP BY.
        #
        # Example:
        #
        # SELECT
        #     customer.name,
        #     SUM(payment.amount)
        #
        # requires:
        #
        # GROUP BY
        #     customer.name
        #
        # Additional grouping identity columns such as
        # customer.id are allowed.
        # --------------------------------------------------

        grouping_signatures = {
            (
                grouping.table,
                grouping.column,
            )

            for grouping
            in plan.groupings
        }

        for projection in (
            plan.projections
        ):

            signature = (
                projection.table,
                projection.column,
            )

            if (
                plan.aggregations
                and signature
                not in grouping_signatures
            ):

                errors.append(
                    "Projected non-aggregate column "
                    "is missing from GROUP BY: "
                    f"{projection.table}."
                    f"{projection.column}"
                )

        # --------------------------------------------------
        # QueryPlan / compiled SQL GROUP BY agreement
        # --------------------------------------------------

        sql_has_group_by = bool(
            re.search(
                r"\bGROUP\s+BY\b",
                sql,
                re.IGNORECASE,
            )
        )

        if (
            plan.groupings
            and not sql_has_group_by
        ):

            errors.append(
                "QueryPlan contains grouping "
                "definitions but compiled SQL "
                "contains no GROUP BY clause."
            )

        if (
            not plan.groupings
            and sql_has_group_by
        ):

            errors.append(
                "Compiled SQL contains GROUP BY "
                "but QueryPlan contains no "
                "grouping definitions."
            )

        # --------------------------------------------------
        # Scalar aggregate sanity
        #
        # Existing scalar aggregate queries should remain:
        #
        # projections = []
        # groupings   = []
        #
        # This protects GQ001 / GQ002 / GQ005 / GQ007.
        # --------------------------------------------------

        if (
            plan.aggregations
            and not plan.groupings
            and plan.projections
        ):

            errors.append(
                "Scalar aggregate QueryPlan cannot "
                "contain non-aggregate projections."
            )

        # --------------------------------------------------
        # Final result
        # --------------------------------------------------

        return SQLValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
        )

    # ======================================================
    # Table extraction
    # ======================================================

    def _extract_tables(
        self,
        sql: str,
    ) -> set[str]:
        """
        Extract schema-qualified tables appearing after:

        FROM
        JOIN

        Example:

        FROM public.erp_customer_details AS t0
        INNER JOIN public.erp_payment_receipt AS t1

        returns:

        {
            "public.erp_customer_details",
            "public.erp_payment_receipt",
        }
        """

        return {
            match.group(1)
            for match
            in self.TABLE_PATTERN.finditer(
                sql
            )
        }