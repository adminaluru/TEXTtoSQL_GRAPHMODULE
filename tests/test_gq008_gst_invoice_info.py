from ai.providers.mock_chat import (
    MockChatModel,
)

from agent.factory import (
    build_research_agent,
)

from database.neo4j import (
    Neo4jClient,
)

from execution.postgres_executor import (
    PostgreSQLExecutor,
)


def test_gq008_gst_invoice_info_end_to_end():

    structured_response = {
        "intent":
            "list",

        "concepts": [
            "Invoice",
            "GSTInvoiceInfo",
        ],

        "result_concept":
            "GSTInvoiceInfo",

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
            "Show GST information for invoices."
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
            == 4
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
            == 4
        )

        # ==================================================
        # Ontology-driven bounded GST projection
        # ==================================================

        expected_columns = {
            "invoice_num",
            "inv_classif",
            "supply_type",
            "gst_status",
            "filing_period",
        }

        for row in response.data:

            assert (
                set(row.keys())
                == expected_columns
            )

        # ==================================================
        # Basic fixture integrity
        # ==================================================

        invoice_nums = [
            row["invoice_num"]
            for row in response.data
        ]

        assert all(
            invoice_num
            for invoice_num
            in invoice_nums
        )

        assert all(
            row["inv_classif"]
            is not None
            for row in response.data
        )

        assert all(
            row["filing_period"]
            is not None
            for row in response.data
        )

    finally:

        neo4j.close()