import pytest

pytestmark = pytest.mark.integration

from ai.providers.mock_chat import MockChatModel

from agent.factory import build_research_agent

from database.neo4j import Neo4jClient

from execution.postgres_executor import (
    PostgreSQLExecutor,
)


def test_gq009_customers_with_claims_end_to_end():

    structured_response = {
        "intent": "list",

        "concepts": [
            "Customer",
            "Invoice",
            "InvoiceClaim",
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
                    statement_timeout_ms=5000,
                    max_rows=500,
                ),
        )

        response = agent.ask(
            "Which customers have invoices with claims?"
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
            == 18
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
            len(response.data)
            == 18
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
                set(row.keys())
                == expected_columns
            )

        # ==================================================
        # EXISTS must preserve Customer grain
        # ==================================================

        customer_nums = [
            row["customer_num"]
            for row in response.data
        ]

        assert (
            len(customer_nums)
            ==
            len(set(customer_nums))
        )

        assert all(
            customer_num
            for customer_num
            in customer_nums
        )

    finally:

        neo4j.close()