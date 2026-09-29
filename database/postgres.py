from typing import Any

import psycopg
from psycopg.rows import dict_row

from config import settings


class PostgresClient:
    """
    Generic PostgreSQL infrastructure client.

    Responsibilities:
    - verify PostgreSQL connectivity
    - retrieve generic database metadata
    - execute generic read-only diagnostic queries

    Business-specific table knowledge must NOT live here.

    Application query execution is handled separately by
    PostgreSQLExecutor.
    """

    def __init__(
        self,
    ) -> None:

        self._dsn = (
            settings.postgres_dsn
        )

    # ======================================================
    # Health
    # ======================================================

    def health_check(
        self,
    ) -> bool:
        """
        Verify PostgreSQL connectivity.
        """

        try:

            with psycopg.connect(
                self._dsn
            ) as conn:

                with conn.cursor() as cursor:

                    cursor.execute(
                        "SELECT 1"
                    )

                    value = (
                        cursor.fetchone()
                    )

                    return (
                        value is not None
                    )

        except Exception as exc:

            print(
                "PostgreSQL health check failed: "
                f"{exc}"
            )

            return False

    # ======================================================
    # Database metadata
    # ======================================================

    def get_database_info(
        self,
    ) -> dict[str, Any]:
        """
        Return generic PostgreSQL server/database metadata.
        """

        query = """
        SELECT
            current_database()
                AS database_name,

            current_user
                AS current_user,

            version()
                AS version
        """

        rows = self.execute_read(
            query
        )

        if not rows:

            return {}

        return rows[0]

    # ======================================================
    # Generic diagnostic read
    # ======================================================

    def execute_read(
        self,
        query: str,
        parameters: tuple | list | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a generic PostgreSQL read query.

        This is intended for infrastructure diagnostics.

        Research Agent business-query execution uses
        PostgreSQLExecutor instead.
        """

        parameters = (
            parameters
            or ()
        )

        with psycopg.connect(
            self._dsn,
            row_factory=dict_row,
        ) as conn:

            with conn.cursor() as cursor:

                cursor.execute(
                    query,
                    parameters,
                )

                rows = cursor.fetchall()

                return [
                    dict(row)
                    for row
                    in rows
                ]


# ==========================================================
# Backward-compatible class alias
#
# This prevents older imports from breaking if some local
# script used PostgreSQLClient instead of PostgresClient.
# ==========================================================

PostgreSQLClient = PostgresClient