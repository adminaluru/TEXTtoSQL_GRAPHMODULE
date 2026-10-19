import pytest

pytestmark = pytest.mark.integration

from decimal import Decimal

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


# ==========================================================
# GQ003
#
# Show invoices for Blue Prism Technologies LE-1001.
#
# Capability:
#
#     LIST entity
#     +
#     bounded ontology-driven default projection
#
# Result concept:
#
#     Invoice
#
# Filter:
#
#     Customer.name ILIKE '%Blue Prism Technologies LE-1001%'
#
# Default Invoice projection comes entirely from Neo4j:
#
#     invoice_num
#     amount
#     balance
#     inv_status
#
# No Invoice-specific projection mapping exists in Python.
# ==========================================================


def test_gq003_invoice_list_end_to_end():

    structured_response = {
        "intent":
            "list",

        "concepts": [
            "Customer",
            "Invoice",
        ],

        "result_concept":
            "Invoice",

        "measure_concept":
            None,

        "measure_attribute":
            None,

        "group_by_concepts":
            [],

        "entities": [
            {
                "concept":
                    "Customer",

                "attribute":
                    "customer name",

                "value":
                    "Blue Prism Technologies LE-1001",
            }
        ],

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
            "Show invoices for Blue Prism Technologies LE-1001."
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
        # Bounded projection contract
        # ==================================================

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

        # ==================================================
        # Order-independent Golden lookup
        # ==================================================

        invoices = {
            row["invoice_num"]:
                row

            for row
            in response.data
        }

        assert (
            len(invoices)
            == 4
        )

        # ==================================================
        # Representative Golden values
        # ==================================================

        assert (
            "SYNINV000101"
            in invoices
        )

        assert (
            Decimal(
                str(
                    invoices[
                        "SYNINV000101"
                    ]["amount"]
                )
            )
            ==
            Decimal("1000.00")
        )

        assert (
            Decimal(
                str(
                    invoices[
                        "SYNINV000101"
                    ]["balance"]
                )
            )
            ==
            Decimal("0.00")
        )

        assert (
            invoices[
                "SYNINV000101"
            ]["inv_status"]
            ==
            "CLOSE"
        )

        assert (
            "SYNINV000104"
            in invoices
        )

        assert (
            Decimal(
                str(
                    invoices[
                        "SYNINV000104"
                    ]["amount"]
                )
            )
            ==
            Decimal("8000.00")
        )

        assert (
            Decimal(
                str(
                    invoices[
                        "SYNINV000104"
                    ]["balance"]
                )
            )
            ==
            Decimal("4900.00")
        )

        assert (
            invoices[
                "SYNINV000104"
            ]["inv_status"]
            ==
            "OPEN"
        )

    finally:

        neo4j.close()