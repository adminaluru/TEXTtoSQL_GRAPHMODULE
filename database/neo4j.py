from typing import Any

from neo4j import (
    Driver,
    GraphDatabase,
)

from config import settings


class Neo4jClient:
    """
    Generic Neo4j infrastructure client.

    Responsibilities:
    - manage Neo4j driver lifecycle
    - verify connectivity
    - execute generic Cypher reads
    - execute generic Cypher writes
    - provide generic graph-level diagnostics

    This class must NOT contain business-specific
    concepts, tables, paths, joins, or ontology rules.

    Business semantics belong in:
    - OntologyService
    - ConceptCatalogService
    - GraphPlanner
    - other higher-level services
    """

    def __init__(
        self,
    ) -> None:

        self._driver: Driver = (
            GraphDatabase.driver(
                settings.neo4j_uri,

                auth=(
                    settings.neo4j_user,

                    settings
                    .neo4j_password
                    .get_secret_value(),
                ),
            )
        )

        self._database = (
            settings.neo4j_database
        )

    # ======================================================
    # Health
    # ======================================================

    def health_check(
        self,
    ) -> bool:
        """
        Verify Neo4j connectivity.
        """

        try:

            self._driver.verify_connectivity()

            return True

        except Exception as exc:

            print(
                "Neo4j health check failed: "
                f"{exc}"
            )

            return False

    # ======================================================
    # Generic graph summary
    # ======================================================

    def get_graph_summary(
        self,
    ) -> dict[str, int]:
        """
        Return generic graph-level counts.

        This method intentionally knows nothing about
        any particular business concept or physical table.
        """

        query = """
        CALL () {
            MATCH (t:Table)
            RETURN count(t) AS table_count
        }

        CALL () {
            MATCH ()-[r:JOINS_TO]->()
            RETURN count(r) AS join_count
        }

        CALL () {
            MATCH (c:Column)
            RETURN count(c) AS column_count
        }

        CALL () {
            MATCH (:Table)-[r:HAS_COLUMN]->(:Column)
            RETURN count(r) AS has_column_count
        }

        RETURN
            table_count,
            join_count,
            column_count,
            has_column_count
        """

        rows = self.execute_read(
            query,
            {},
        )

        if not rows:

            return {
                "table_count": 0,
                "join_count": 0,
                "column_count": 0,
                "has_column_count": 0,
            }

        row = rows[0]

        return {
            "table_count":
                int(
                    row.get(
                        "table_count",
                        0,
                    )
                ),

            "join_count":
                int(
                    row.get(
                        "join_count",
                        0,
                    )
                ),

            "column_count":
                int(
                    row.get(
                        "column_count",
                        0,
                    )
                ),

            "has_column_count":
                int(
                    row.get(
                        "has_column_count",
                        0,
                    )
                ),
        }

    # ======================================================
    # Generic read
    # ======================================================

    def execute_read(
        self,
        query: str,
        parameters: dict | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute Cypher and return plain dictionaries.

        Higher-level business/ontology logic must remain
        outside this infrastructure client.
        """

        parameters = (
            parameters
            or {}
        )

        with self._driver.session(
            database=self._database
        ) as session:

            result = session.run(
                query,
                parameters,
            )

            return [
                record.data()
                for record
                in result
            ]

    # ======================================================
    # Generic write
    # ======================================================

    def execute_write(
        self,
        query: str,
        parameters: dict | None = None,
    ) -> None:
        """
        Execute one controlled Cypher write statement.

        The infrastructure client performs no business
        interpretation of the statement.
        """

        parameters = (
            parameters
            or {}
        )

        with self._driver.session(
            database=self._database
        ) as session:

            result = session.run(
                query,
                parameters,
            )

            # Force execution before session closure and
            # surface server-side errors immediately.
            result.consume()

    # ======================================================
    # Close
    # ======================================================

    def close(
        self,
    ) -> None:
        """
        Close the Neo4j driver.
        """

        self._driver.close()