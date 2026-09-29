from planning.models import QueryPlan

from sql_compiler.models import CompiledQuery
from sql_validator.models import SQLValidationResult

from sql_guardrails.models import GuardrailResult


class SQLGuardrails:
    """
    Deterministic execution-policy layer.

    This component does NOT:
    - generate SQL
    - modify SQL
    - call an LLM
    - call Neo4j
    - call PostgreSQL
    - resolve tables or columns

    It decides whether an already compiled and validated
    query is allowed to proceed to execution.
    """

    # ======================================================
    # Policy
    # ======================================================

    ALLOWED_SCHEMAS = {
        "public",
    }

    ALLOWED_JOIN_TYPES = {
        "INNER",
        "LEFT",
    }

    SAFE_JOIN_STATUSES = {
        "confirmed",
    }

    MAX_TABLES = 8

    MAX_JOINS = 7

    MAX_EXISTENCE_CONDITIONS = 4

    MAX_PARAMETERS = 100

    MAX_LIST_LIMIT = 500

    DEFAULT_EXPECTED_LIST_LIMIT = 100

    # ======================================================
    # Public API
    # ======================================================

    def evaluate(
        self,
        compiled: CompiledQuery,
        plan: QueryPlan,
        validation: SQLValidationResult,
    ) -> GuardrailResult:
        """
        Apply execution policy to a validated query.

        A query must first pass SQLValidator.

        Guardrails fail closed:
        anything outside the permitted execution policy
        becomes a violation.
        """

        violations: list[str] = []
        warnings: list[str] = []

        # --------------------------------------------------
        # 1. Validator must already approve the SQL
        # --------------------------------------------------

        if not validation.valid:

            violations.append(
                "SQLValidator rejected the query."
            )

            violations.extend(
                validation.errors
            )

            return GuardrailResult(
                allowed=False,
                violations=self._unique(
                    violations
                ),
                warnings=warnings,
            )

        # --------------------------------------------------
        # 2. Authorized executable scope
        # --------------------------------------------------

        all_tables = list(
            plan.tables
        )

        for condition in plan.existence_conditions:

            all_tables.extend(
                condition.tables
            )

        all_tables = self._unique(
            all_tables
        )

        all_joins = list(
            plan.joins
        )

        for condition in plan.existence_conditions:

            all_joins.extend(
                condition.joins
            )

        # --------------------------------------------------
        # 3. Approved schema only
        # --------------------------------------------------

        for table in all_tables:

            schema = self._extract_schema(
                table
            )

            if schema not in self.ALLOWED_SCHEMAS:

                violations.append(
                    "Table references unauthorized schema: "
                    f"{table}"
                )

        # --------------------------------------------------
        # 4. Table fan-out policy
        # --------------------------------------------------

        if len(all_tables) > self.MAX_TABLES:

            violations.append(
                "Query touches too many tables: "
                f"{len(all_tables)} > {self.MAX_TABLES}"
            )

        # --------------------------------------------------
        # 5. Existence-condition count policy
        # --------------------------------------------------

        if (
            len(plan.existence_conditions)
            > self.MAX_EXISTENCE_CONDITIONS
        ):

            violations.append(
                "Query contains too many existence "
                "conditions: "
                f"{len(plan.existence_conditions)} > "
                f"{self.MAX_EXISTENCE_CONDITIONS}"
            )

        # --------------------------------------------------
        # 6. Join-count policy
        # --------------------------------------------------

        if len(all_joins) > self.MAX_JOINS:

            violations.append(
                "Query contains too many joins: "
                f"{len(all_joins)} > {self.MAX_JOINS}"
            )

        # --------------------------------------------------
        # 7. Join type + trust policy
        # --------------------------------------------------

        for join in all_joins:

            if (
                join.join_type
                not in self.ALLOWED_JOIN_TYPES
            ):

                violations.append(
                    "Unsupported execution join type: "
                    f"{join.join_type}"
                )

            status = (
                join.validation_status
                or "unknown"
            )

            if status not in self.SAFE_JOIN_STATUSES:

                violations.append(
                    "Execution blocked for join with "
                    "non-confirmed validation status: "
                    f"{status}"
                )

        # --------------------------------------------------
        # 8. Parameter-count policy
        # --------------------------------------------------

        parameter_count = len(
            compiled.parameters
        )

        if parameter_count > self.MAX_PARAMETERS:

            violations.append(
                "Query contains too many parameters: "
                f"{parameter_count} > "
                f"{self.MAX_PARAMETERS}"
            )

        # --------------------------------------------------
        # 9. List / lookup queries must be bounded
        # --------------------------------------------------

        if plan.intent in {
            "list",
            "lookup",
        }:

            if plan.limit is None:

                violations.append(
                    "List/lookup query must have "
                    "an explicit execution limit."
                )

            elif plan.limit > self.MAX_LIST_LIMIT:

                violations.append(
                    "List/lookup limit exceeds policy: "
                    f"{plan.limit} > "
                    f"{self.MAX_LIST_LIMIT}"
                )

        # --------------------------------------------------
        # 10. Aggregate/count queries normally should not
        # carry a broad list-style LIMIT.
        # --------------------------------------------------

        if plan.intent in {
            "aggregate",
            "count",
        }:

            if (
                plan.limit is not None
                and plan.limit > 1
            ):

                warnings.append(
                    "Aggregate/count query contains "
                    f"LIMIT {plan.limit}; verify that "
                    "this is intentional."
                )

        # --------------------------------------------------
        # 11. Query must remain parameterized
        # --------------------------------------------------

        if compiled.parameters:

            if "%s" not in compiled.sql:

                violations.append(
                    "Runtime parameters exist but SQL "
                    "contains no parameter placeholders."
                )

        # --------------------------------------------------
        # Final policy decision
        # --------------------------------------------------

        return GuardrailResult(
            allowed=not violations,
            violations=self._unique(
                violations
            ),
            warnings=self._unique(
                warnings
            ),
        )

    # ======================================================
    # Utilities
    # ======================================================

    @staticmethod
    def _extract_schema(
        table_key: str,
    ) -> str:
        """
        public.erp_customer_details
            ->
        public
        """

        parts = table_key.split(
            ".",
            1,
        )

        if len(parts) != 2:

            return ""

        return parts[0]

    @staticmethod
    def _unique(
        values: list[str],
    ) -> list[str]:

        seen = set()
        result = []

        for value in values:

            if value not in seen:

                seen.add(
                    value
                )

                result.append(
                    value
                )

        return result