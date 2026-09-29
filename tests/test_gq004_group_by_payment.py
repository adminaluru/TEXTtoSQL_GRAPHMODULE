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
# Golden PostgreSQL truth:
#
#     18 physical Customer entities
#     4000.00 payment total per entity
#     72000.00 overall
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
        # 18 × 4000.00 = 72000.00
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
            == 18
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