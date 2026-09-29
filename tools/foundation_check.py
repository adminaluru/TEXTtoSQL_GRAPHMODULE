from database.neo4j import (
    Neo4jClient,
)

from database.postgres import (
    PostgresClient,
)


def separator() -> None:

    print(
        "-" * 70
    )


def main() -> None:

    print(
        "=" * 70
    )

    print(
        "RESEARCH AGENT FOUNDATION CHECK"
    )

    print(
        "=" * 70
    )

    # ======================================================
    # PostgreSQL
    # ======================================================

    postgres = PostgresClient()

    print()
    print(
        "POSTGRESQL"
    )
    separator()

    postgres_ok = (
        postgres.health_check()
    )

    print(
        "Connection:",
        (
            "OK"
            if postgres_ok
            else "FAILED"
        ),
    )

    if postgres_ok:

        database_info = (
            postgres.get_database_info()
        )

        print(
            "Database:",
            database_info.get(
                "database_name"
            ),
        )

        print(
            "User:",
            database_info.get(
                "current_user"
            ),
        )

    # ======================================================
    # Neo4j
    # ======================================================

    neo4j = Neo4jClient()

    try:

        print()
        print(
            "NEO4J"
        )
        separator()

        neo4j_ok = (
            neo4j.health_check()
        )

        print(
            "Connection:",
            (
                "OK"
                if neo4j_ok
                else "FAILED"
            ),
        )

        if neo4j_ok:

            summary = (
                neo4j.get_graph_summary()
            )

            print(
                "Table Nodes:",
                summary[
                    "table_count"
                ],
            )

            print(
                "Column Nodes:",
                summary[
                    "column_count"
                ],
            )

            print(
                "JOINS_TO:",
                summary[
                    "join_count"
                ],
            )

            print(
                "HAS_COLUMN:",
                summary[
                    "has_column_count"
                ],
            )

            # ==============================================
            # Business concept catalog
            #
            # Dynamic — no concept names hard-coded.
            # ==============================================

            concept_rows = (
                neo4j.execute_read(
                    """
                    MATCH (t:Table)

                    WHERE
                        t.business_concept
                        IS NOT NULL

                    RETURN
                        count(
                            DISTINCT
                            t.business_concept
                        ) AS concept_count
                    """,
                    {},
                )
            )

            concept_count = (
                concept_rows[0][
                    "concept_count"
                ]
                if concept_rows
                else 0
            )

            print(
                "Business Concepts:",
                concept_count,
            )

            # ==============================================
            # Semantic relationship count
            #
            # Dynamic — excludes physical/column metadata.
            # ==============================================

            semantic_rows = (
                neo4j.execute_read(
                    """
                    MATCH
                        (:Table)-[r]->(:Table)

                    WHERE
                        type(r) <> 'JOINS_TO'
                        AND type(r) <> 'HAS_COLUMN'

                    RETURN
                        count(r)
                        AS semantic_count
                    """,
                    {},
                )
            )

            semantic_count = (
                semantic_rows[0][
                    "semantic_count"
                ]
                if semantic_rows
                else 0
            )

            print(
                "Semantic Relationships:",
                semantic_count,
            )

    finally:

        neo4j.close()

    # ======================================================
    # Status
    # ======================================================

    print()
    print(
        "=" * 70
    )

    if (
        postgres_ok
        and neo4j_ok
    ):

        print(
            "FOUNDATION STATUS: READY"
        )

    else:

        print(
            "FOUNDATION STATUS: NOT READY"
        )

    print(
        "=" * 70
    )


if __name__ == "__main__":
    main()