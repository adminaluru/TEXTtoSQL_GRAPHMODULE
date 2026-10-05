from time import perf_counter

import psycopg
from psycopg.rows import dict_row

from config import settings

from execution.models import (
    PostgreSQLExecutionResult,
)

from sql_compiler.models import (
    CompiledQuery,
)

from sql_guardrails.models import (
    GuardrailResult,
)

from sql_validator.models import (
    SQLValidationResult,
)


class PostgreSQLExecutor:
    """
    Controlled PostgreSQL execution boundary.

    Responsibilities:

    - execute only validated + guardrail-approved queries
    - use parameterized SQL
    - execute inside a READ ONLY transaction
    - enforce PostgreSQL statement timeout
    - bound returned result rows
    - return plain structured data

    Does NOT:

    - generate SQL
    - modify SQL
    - call an LLM
    - call Neo4j
    - resolve tables
    - resolve columns
    - perform business reasoning
    """

    def __init__(
        self,
        statement_timeout_ms: int = 5000,
        max_rows: int = 500,
        dsn: str | None = None,
    ) -> None:

        if statement_timeout_ms < 1:
            raise ValueError(
                "statement_timeout_ms must "
                "be greater than zero."
            )

        if max_rows < 1:
            raise ValueError(
                "max_rows must be greater than zero."
            )

        self._dsn = (
            dsn
            or settings.postgres_dsn
        )

        self._statement_timeout_ms = (
            statement_timeout_ms
        )

        self._max_rows = max_rows

    def health_check(self) -> bool:
        """Check whether PostgreSQL is reachable."""
        try:
            with psycopg.connect(self._dsn) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            return True
        except Exception:
            return False

    # ======================================================
    # Public API
    # ======================================================

    def execute(
        self,
        compiled: CompiledQuery,
        validation: SQLValidationResult,
        guardrails: GuardrailResult,
    ) -> PostgreSQLExecutionResult:
        """
        Execute an approved compiled query.

        Execution fails closed:

        SQLValidator must be valid
        AND
        SQLGuardrails must allow execution.
        """

        # --------------------------------------------------
        # 1. Validator gate
        # --------------------------------------------------

        if not validation.valid:

            raise PermissionError(
                "PostgreSQL execution blocked: "
                "SQL validation failed. "
                f"Errors: {validation.errors}"
            )

        # --------------------------------------------------
        # 2. Guardrail gate
        # --------------------------------------------------

        if not guardrails.allowed:

            raise PermissionError(
                "PostgreSQL execution blocked by "
                "SQL guardrails. "
                f"Violations: "
                f"{guardrails.violations}"
            )

        # --------------------------------------------------
        # 3. Execute using a short-lived connection.
        #
        # Current architecture intentionally does not
        # introduce connection pooling yet.
        # --------------------------------------------------

        with psycopg.connect(
            self._dsn,
            row_factory=dict_row,
        ) as conn:

            # ----------------------------------------------
            # Everything runs inside one transaction.
            # ----------------------------------------------

            with conn.transaction():

                with conn.cursor() as cursor:

                    # ======================================
                    # DATABASE-LEVEL SAFETY
                    #
                    # Even if upstream validation were
                    # incorrectly bypassed, PostgreSQL
                    # itself rejects INSERT/UPDATE/DELETE
                    # etc. inside this transaction.
                    # ======================================

                    cursor.execute(
                        "SET TRANSACTION READ ONLY"
                    )

                    # ======================================
                    # Statement timeout
                    #
                    # set_config(..., true) applies only
                    # to the current transaction.
                    # ======================================

                    cursor.execute(
                        """
                        SELECT set_config(
                            'statement_timeout',
                            %s,
                            true
                        )
                        """,
                        (
                            (
                                f"{self._statement_timeout_ms}"
                                "ms"
                            ),
                        ),
                    )

                    # ======================================
                    # Execute parameterized SQL
                    # ======================================

                    started = perf_counter()

                    cursor.execute(
                        compiled.sql,
                        compiled.parameters,
                    )

                    # ======================================
                    # Controlled result retrieval
                    #
                    # Fetch one extra row so we know if
                    # the result exceeded our boundary.
                    # ======================================

                    fetched_rows = (
                        cursor.fetchmany(
                            self._max_rows + 1
                        )
                    )

                    elapsed_ms = (
                        perf_counter()
                        - started
                    ) * 1000.0

                    # ======================================
                    # Determine truncation
                    # ======================================

                    truncated = (
                        len(fetched_rows)
                        > self._max_rows
                    )

                    if truncated:

                        fetched_rows = (
                            fetched_rows[
                                :self._max_rows
                            ]
                        )

                    # ======================================
                    # Result column names
                    # ======================================

                    if cursor.description:

                        columns = [
                            description.name
                            for description
                            in cursor.description
                        ]

                    else:

                        columns = []

                    # ======================================
                    # Return plain structured result
                    # ======================================

                    return (
                        PostgreSQLExecutionResult(
                            columns=columns,

                            rows=[
                                dict(row)
                                for row
                                in fetched_rows
                            ],

                            row_count=len(
                                fetched_rows
                            ),

                            truncated=truncated,

                            execution_ms=
                                round(
                                    elapsed_ms,
                                    3,
                                ),
                        )
                    )

