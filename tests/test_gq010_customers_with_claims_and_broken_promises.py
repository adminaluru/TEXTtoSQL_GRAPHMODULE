from ai.providers.mock_chat import MockChatModel

from agent.factory import build_research_agent

from database.neo4j import Neo4jClient

from execution.postgres_executor import (
    PostgreSQLExecutor,
)


def test_gq010_customers_with_claims_and_broken_promises_end_to_end():

    structured_response = {
        "intent": "list",

        "concepts": [
            "Customer",
            "InvoiceClaim",
            "BrokenPromiseToPay",
        ],

        "result_concept":
            "Customer",

        "measure_concept":
            None,

        "measure_attribute":
            None,

        "group_by_concepts":
            [],

        "entities":
            [],

        "aggregation":
            None,
    }

    neo4j = Neo4jClient()

    try:

        agent = build_research_agent(
            chat_model=
                MockChatModel(
                    structured_response=
                        structured_response
                ),

            neo4j_client=
                neo4j,

            postgres_executor=
                PostgreSQLExecutor(
                    statement_timeout_ms=
                        5000,

                    max_rows=
                        500,
                ),
        )

        response = agent.ask(
            "Which customers have both claims and broken promises?"
        )

        # ==================================================
        # Result contract
        # ==================================================

        assert (
            response.result_type
            == "tabular"
        )

        assert (
            response.row_count
            == 9
        )

        assert (
            response.warnings
            == []
        )

        assert (
            response.data
            is not None
        )

        assert (
            len(
                response.data
            )
            == 9
        )

        # ==================================================
        # Customer bounded projection
        # ==================================================

        expected_columns = {
            "name",
            "customer_num",
            "account_status",
            "city",
            "country_code",
        }

        for row in response.data:

            assert (
                set(
                    row.keys()
                )
                ==
                expected_columns
            )

        # ==================================================
        # Customer grain must remain unique
        # ==================================================

        customer_nums = [
            row[
                "customer_num"
            ]

            for row
            in response.data
        ]

        assert (
            len(
                customer_nums
            )
            ==
            len(
                set(
                    customer_nums
                )
            )
        )

        # ==================================================
        # Basic data sanity
        # ==================================================

        assert all(
            row["name"]
            for row
            in response.data
        )

        assert all(
            row["customer_num"]
            for row
            in response.data
        )

    finally:

        neo4j.close()