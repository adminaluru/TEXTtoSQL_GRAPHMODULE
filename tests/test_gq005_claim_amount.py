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
# GQ005
#
# What is the total claim amount for Blue Prism Technologies LE-1001?
#
# Capability:
#
#     Multi-hop SUM
#
# Semantic path:
#
#     Customer
#         ↓
#     Invoice
#         ↓
#     InvoiceClaim
#
# IMPORTANT:
#
# Invoice is NOT supplied by the LLM.
#
# GraphPlanner must discover the intermediate
# Invoice bridge dynamically.
#
# Golden PostgreSQL value:
#
#     500.00
# ==========================================================


def test_gq005_claim_amount_multi_hop_end_to_end():

    structured_response = {
        "intent":
            "aggregate",

        "concepts": [
            "Customer",
            "InvoiceClaim",
        ],

        "result_concept":
            "Customer",

        "measure_concept":
            "InvoiceClaim",

        "measure_attribute":
            "claim amount",

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
                            100,
                    ),
            )
        )

        response = (
            agent.ask(
                "What is the total claim "
                "amount for Blue Prism Technologies LE-1001?"
            )
        )

        # ==================================================
        # Result contract
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
        # Golden value
        # ==================================================

        assert (
            response.data["alias"]
            == "sum_claim_amount"
        )

        assert (
            Decimal(
                str(
                    response.data["value"]
                )
            )
            ==
            Decimal("500.00")
        )

        # ==================================================
        # Business-facing answer
        # ==================================================

        assert (
            "Blue Prism Technologies LE-1001"
            in response.answer
        )

        assert (
            "500.00"
            in response.answer
        )

        # Physical implementation details should not leak.
        assert (
            "d_claim_invoice"
            not in response.answer
        )

        assert (
            "claim_amount"
            not in response.answer
        )

    finally:

        neo4j.close()