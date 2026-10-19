from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from config import settings

from observability.deadline import (
    DeadlineExceeded,
    cap_timeout,
    remaining_seconds,
)

from execution.models import (
    DatabasePrivileges,
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
    - enforce PostgreSQL statement and lock timeouts, never
      longer than the time left for the whole question
    - report timeouts and lost connections as TimeoutError /
      ConnectionError with safe messages (no SQL, no DSN)
    - bound returned result rows
    - return plain structured data
    - reuse connections through an optional pool

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
        pool: ConnectionPool | None = None,
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

        # When a pool is supplied, connections are reused.
        # READ ONLY and statement_timeout are both
        # transaction-scoped, so nothing leaks between
        # pooled requests.
        self._pool = pool

    def health_check(self) -> bool:
        """Check whether PostgreSQL is reachable."""
        try:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            return True
        except Exception:
            return False

    def privileges(self, schema: str = "public") -> DatabasePrivileges:
        """
        Report what this login may do (Step 7, least privilege).

        Read-only: one catalog query inside a READ ONLY
        transaction. Used at start-up to refuse an unsafe
        production login.
        """

        with self._connection() as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(
                        """
                        SELECT
                            current_user,
                            r.rolsuper,
                            r.rolbypassrls,
                            (
                                SELECT count(*)
                                FROM pg_class c
                                JOIN pg_namespace n ON n.oid = c.relnamespace
                                WHERE n.nspname = %s
                                  AND c.relkind IN ('r', 'p')
                                  AND (
                                      has_table_privilege(c.oid, 'INSERT')
                                      OR has_table_privilege(c.oid, 'UPDATE')
                                      OR has_table_privilege(c.oid, 'DELETE')
                                      OR has_table_privilege(c.oid, 'TRUNCATE')
                                  )
                            ),
                            has_schema_privilege(%s, 'CREATE')
                            OR has_database_privilege(current_database(), 'CREATE')
                        FROM pg_roles r
                        WHERE r.rolname = current_user
                        """,
                        (schema, schema),
                    )
                    role, superuser, bypass, writable, create = cursor.fetchone()

        return DatabasePrivileges(
            role=role,
            is_superuser=superuser,
            bypasses_row_security=bypass,
            writable_tables=writable,
            can_create=create,
        )

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        """
        Pooled connection when configured, otherwise a
        short-lived connection.
        """

        if self._pool is None:

            with psycopg.connect(self._dsn) as conn:
                yield conn

            return

        try:
            # Waiting for a free pooled connection also counts
            # against the question's deadline.
            with self._pool.connection(
                timeout=cap_timeout(self._pool.timeout)
            ) as conn:
                yield conn
        except PoolTimeout as exc:
            # Surfaces as 503 rather than 500 at the API.
            raise TimeoutError(
                "PostgreSQL connection pool exhausted."
            ) from exc

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
        # 3. Execute using a pooled or short-lived
        #    connection, within the question's deadline.
        # --------------------------------------------------

        timeout_ms = max(
            1,
            int(
                cap_timeout(self._statement_timeout_ms / 1000.0)
                * 1000
            ),
        )

        try:
            return self._run(compiled, timeout_ms)

        except (pg_errors.QueryCanceled, pg_errors.LockNotAvailable) as exc:
            # PostgreSQL cancelled the statement (statement or
            # lock timeout). Never echo the database message: it
            # can contain SQL text and parameter values.
            remaining = remaining_seconds()
            if remaining is not None and remaining <= 0.05:
                raise DeadlineExceeded() from exc
            raise TimeoutError(
                "PostgreSQL statement timed out."
            ) from exc

        except psycopg.OperationalError as exc:
            raise ConnectionError(
                "PostgreSQL unavailable."
            ) from exc

    def _run(
        self,
        compiled: CompiledQuery,
        timeout_ms: int,
    ) -> PostgreSQLExecutionResult:

        with self._connection() as conn:

            # ----------------------------------------------
            # Everything runs inside one transaction.
            # ----------------------------------------------

            with conn.transaction():

                with conn.cursor(
                    row_factory=dict_row,
                ) as cursor:

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
                        SELECT
                            set_config('statement_timeout', %s, true),
                            set_config('lock_timeout', %s, true)
                        """,
                        (
                            f"{timeout_ms}ms",
                            f"{timeout_ms}ms",
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

