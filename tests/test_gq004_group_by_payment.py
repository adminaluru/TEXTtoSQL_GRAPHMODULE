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
# GQ004
#
# Question:
#
#     What is the total payment amount by customer?
#
# Capability:
#
#     GROUP BY(entity)
#     +
#     SUM(attribute)
#
# Semantic path:
#
#     Customer
#         ↓
#     PaymentReceipt
#
# Physical behavior:
#
#     SELECT
#         Customer.name,
#         SUM(PaymentReceipt.payment_amount)
#
#     GROUP BY
#         Customer.id,
#         Customer.name
#
# Golden PostgreSQL truth (read-only SQL, 2026-10-15):
#
#     21 physical Customer entities
#     18 with payments: 4000.00 USD each, 72000.00 overall
#     3 without payments, listed with 0 (decision D-005):
#         Bharat Industrial Systems
#         Global Tech Imports LLC
#         Horizon India Enterprises
#     currency = linked invoice currency (decision D-004);
#     unknown for customers without payments
#
# NOTE:
#
# Row order is deliberately NOT asserted because the
# generated SQL currently contains no ORDER BY.
# ==========================================================


def test_gq004_payment_amount_by_customer_end_to_end():

    structured_response = {
        "intent":
            "aggregate",

        "concepts": [
            "Customer",
            "PaymentReceipt",
        ],

        "result_concept":
            "Customer",

        "measure_concept":
            "PaymentReceipt",

        "measure_attribute":
            "payment amount",

        "group_by_concepts": [
            "Customer"
        ],

        "entities":
            [],

        "aggregation":
            "SUM",
    }

    neo4j = Neo4jClient()

    try:

        agent = (
            build_research_agent(
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
        )

        response = (
            agent.ask(
                "What is the total payment "
                "amount by customer?"
            )
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
            == 21
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
            == 21
        )

        # ==================================================
        # Every grouped row must expose:
        #
        # customer_name
        # sum_payment_amount
        # ==================================================

        for row in response.data:

            assert (
                "customer_name"
                in row
            )

            assert (
                "sum_payment_amount"
                in row
            )

        # ==================================================
        # Golden reconciliation
        #
        # 18 × 4000.00 + 3 × 0 = 72000.00
        # ==================================================

        total = sum(
            (
                Decimal(
                    str(
                        row[
                            "sum_payment_amount"
                        ]
                    )
                )

                for row
                in response.data
            ),

            Decimal("0.00"),
        )

        assert (
            total
            ==
            Decimal(
                "72000.00"
            )
        )

        # ==================================================
        # Build order-independent lookup.
        # ==================================================

        customer_totals = {
            row["customer_name"]:
                Decimal(
                    str(
                        row[
                            "sum_payment_amount"
                        ]
                    )
                )

            for row
            in response.data
        }

        assert (
            len(customer_totals)
            == 21
        )

        # ==================================================
        # D-005: customers without payments are listed with 0
        # and an unknown currency, never silently dropped.
        # ==================================================

        zero_rows = {
            row["customer_name"]: row
            for row in response.data
            if Decimal(str(row["sum_payment_amount"])) == 0
        }

        assert set(zero_rows) == {
            "Bharat Industrial Systems",
            "Global Tech Imports LLC",
            "Horizon India Enterprises",
        }

        assert all(
            row["currency"] is None
            for row in zero_rows.values()
        )

        assert all(
            row["currency"] == "USD"
            for row in response.data
            if row["customer_name"] not in zero_rows
        )

        # ==================================================
        # Representative Golden customers
        # ==================================================

        assert (
            customer_totals[
                "Blue Prism Technologies LE-1001"
            ]
            ==
            Decimal("4000.00")
        )

        assert (
            customer_totals[
                "Atlas Industrial Systems LE-1001"
            ]
            ==
            Decimal("4000.00")
        )

        assert (
            customer_totals[
                "Northstar Retail Group LE-1001"
            ]
            ==
            Decimal("4000.00")
        )

        assert (
            customer_totals[
                "Vertex Global Services LE-1004"
            ]
            ==
            Decimal("4000.00")
        )

    finally:

        neo4j.close()