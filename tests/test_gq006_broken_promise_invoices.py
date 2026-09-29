from ai.providers.mock_chat import MockChatModel
from agent.factory import build_research_agent
from database.neo4j import Neo4jClient
from execution.postgres_executor import PostgreSQLExecutor


def test_gq006_broken_promise_invoices_end_to_end():

    structured_response = {
        "intent": "list",

        "concepts": [
            "Invoice",
            "BrokenPromiseToPay",
        ],

        "result_concept": "Invoice",

        "measure_concept": None,
        "measure_attribute": None,

        "group_by_concepts": [],

        "entities": [],

        "aggregation": None,
    }

    neo4j = Neo4jClient()

    try:

        agent = build_research_agent(
            chat_model=MockChatModel(
                structured_response=
                    structured_response
            ),

            neo4j_client=neo4j,

            postgres_executor=
                PostgreSQLExecutor(
                    statement_timeout_ms=5000,
                    max_rows=500,
                ),
        )

        response = agent.ask(
            "Which invoices have broken promises to pay?"
        )

        # ----------------------------------------------
        # Result contract
        # ----------------------------------------------

        assert response.result_type == "tabular"
        assert response.row_count == 9
        assert response.warnings == []

        assert response.data is not None
        assert len(response.data) == 9

        # ----------------------------------------------
        # Bounded Invoice projection
        # ----------------------------------------------

        expected_columns = {
            "invoice_num",
            "amount",
            "balance",
            "inv_status",
        }

        for row in response.data:

            assert (
                set(row.keys())
                == expected_columns
            )

        # ----------------------------------------------
        # EXISTS semantics must return unique entities
        # ----------------------------------------------

        invoice_nums = [
            row["invoice_num"]
            for row in response.data
        ]

        assert (
            len(invoice_nums)
            ==
            len(set(invoice_nums))
        )

        # ----------------------------------------------
        # Basic data sanity
        # ----------------------------------------------

        assert all(
            invoice_num is not None
            for invoice_num
            in invoice_nums
        )

    finally:
        neo4j.close()