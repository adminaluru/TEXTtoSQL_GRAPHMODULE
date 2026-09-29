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
# GQ007
#
# Question:
#
#     What is the total invoice adjustment amount?
#
# Semantic capability:
#
#     SUM(attribute)
#
# Business semantic:
#
#     InvoiceAdjustment.adjustment amount
#
# Physical resolution:
#
#     public.erp_inv_adjustments.amount
#
# Verified Golden Data:
#
#     SUM(amount) = 6750.00
# ==========================================================

def test_gq007_invoice_adjustment_total_end_to_end():

    structured_response = {
        "intent":
            "aggregate",

        "concepts": [
            "InvoiceAdjustment",
        ],

        "result_concept":
            "InvoiceAdjustment",

        "measure_concept":
            "InvoiceAdjustment",

        "measure_attribute":
            "adjustment amount",

        "group_by_concepts":
            [],

        "entities":
            [],

        "aggregation":
            "SUM",
    }

    chat_model = (
        MockChatModel(
            structured_response=
                structured_response
        )
    )

    neo4j = (
        Neo4jClient()
    )

    try:

        agent = (
            build_research_agent(
                chat_model=
                    chat_model,

                neo4j_client=
                    neo4j,

                postgres_executor=
                    PostgreSQLExecutor(
                        statement_timeout_ms=
                            5000,

                        max_rows=
                            100,
                    ),
            )
        )

        response = (
            agent.ask(
                "What is the total invoice "
                "adjustment amount?"
            )
        )

        # ==================================================
        # Response contract
        # ==================================================

        assert (
            response.result_type
            == "scalar"
        )

        assert (
            response.row_count
            == 1
        )

        assert (
            response.warnings
            == []
        )

        assert (
            response.data
            is not None
        )

        # ==================================================
        # Deterministic physical aggregation
        #
        # adjustment amount
        #       ↓
        # amount
        #       ↓
        # SUM(amount)
        #       ↓
        # sum_amount
        # ==================================================

        assert (
            response.data["alias"]
            == "sum_amount"
        )

        assert (
            response.data["value"]
            ==
            Decimal(
                "6750.00"
            )
        )

        # ==================================================
        # Business-facing answer
        # ==================================================

        assert (
            response.answer
            ==
            "The total adjustment amount "
            "is 6750.00."
        )

        # Physical implementation details should not
        # dominate the business-facing answer.

        assert (
            "erp_inv_adjustments"
            not in response.answer
        )

    finally:

        neo4j.close()