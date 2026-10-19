import pytest
from decimal import Decimal

from ai.providers.mock_chat import (
    MockChatModel,
)

from agent.factory import (
    build_research_agent,
)

from agent.orchestrator import (
    ResearchAgentOrchestrator,
)

from database.neo4j import (
    Neo4jClient,
)

from execution.postgres_executor import (
    PostgreSQLExecutor,
)


# ==========================================================
# Helper
# ==========================================================

def build_agent(
    structured_response: dict,
):
    """
    Build the complete Research Agent using:

    - MockChatModel only for deterministic LLM output
    - real Neo4j ontology
    - real graph planning
    - real semantic normalization
    - real grain resolution
    - real column resolution
    - real QueryPlanBuilder
    - real SQL compiler
    - real SQL validator
    - real SQL guardrails
    - real PostgreSQL execution
    - real result analysis
    - real answer service

    The mock exists only at the LLM/provider boundary.
    """

    chat_model = (
        MockChatModel(
            structured_response=
                structured_response
        )
    )

    neo4j = (
        Neo4jClient()
    )

    executor = (
        PostgreSQLExecutor(
            statement_timeout_ms=
                5000,

            max_rows=
                100,
        )
    )

    agent = (
        build_research_agent(
            chat_model=
                chat_model,

            neo4j_client=
                neo4j,

            postgres_executor=
                executor,
        )
    )

    return (
        agent,
        neo4j,
    )


# ==========================================================
# GQ001
#
# SUM(attribute)
#
# Question:
#
#     How much has Blue Prism Technologies LE-1001 paid?
#
# Temporary POC entity matching:
#
#     Customer.name ILIKE '%Blue Prism Technologies LE-1001%'
#
# Verified PostgreSQL Golden Data:
#
#     matching customer records = 1 (SYN-CUST-1001-01#527#1001)
#     payment total             = 4000.00
# ==========================================================

@pytest.mark.integration
def test_research_agent_customer_payment_end_to_end():

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

        "group_by_concepts":
            [],

        "entities": [
            {
                "concept":
                    "Customer",

                # Real LLM wording may include the
                # business concept prefix.
                #
                # ColumnResolver must normalize:
                #
                # customer name
                #       ↓
                # name
                "attribute":
                    "customer name",

                "value":
                    "Blue Prism Technologies LE-1001",
            }
        ],

        "aggregation":
            "SUM",
    }

    agent, neo4j = (
        build_agent(
            structured_response
        )
    )

    try:

        response = (
            agent.ask(
                "How much has "
                "Blue Prism Technologies LE-1001 paid?"
            )
        )

        # ==================================================
        # Final response contract
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
        # Physical aggregate contract
        #
        # Semantic:
        #
        # SUM PaymentReceipt.payment amount
        #
        # Physical:
        #
        # SUM(payment_amount)
        # ==================================================

        assert (
            response.data["alias"]
            ==
            "sum_payment_amount"
        )

        # ==================================================
        # Current verified Golden value
        # ==================================================

        assert (
            response.data["value"]
            ==
            Decimal(
                "4000.00"
            )
        )

        # ==================================================
        # Business-facing answer
        # ==================================================

        assert (
            response.answer
            ==
            "The total payment amount "
            "for Blue Prism Technologies LE-1001 is 4000.00 USD."
        )

        assert (
            "Blue Prism Technologies LE-1001"
            in response.answer
        )

        assert (
            "payment amount"
            in response.answer
        )

    finally:

        neo4j.close()


# ==========================================================
# GQ002
#
# COUNT(entity)
#
# Question:
#
#     How many invoices does Blue Prism Technologies LE-1001 have?
#
# Raw LLM wording deliberately contains:
#
#     measure_attribute = "invoice count"
#
# SemanticQueryNormalizer MUST convert this into:
#
#     COUNT
#     target_kind = entity
#     concept = Invoice
#     attribute = None
#
# Therefore ColumnResolver must never try to resolve:
#
#     "invoice count"
#
# as a physical column.
#
# Temporary POC entity matching:
#
#     Customer.name ILIKE '%Blue Prism Technologies LE-1001%'
#
# Verified PostgreSQL Golden Data:
#
#     matching customer records = 1 (SYN-CUST-1001-01#527#1001)
#     invoice count             = 4
# ==========================================================

@pytest.mark.integration
def test_research_agent_invoice_entity_count_end_to_end():

    structured_response = {
        "intent":
            "count",

        "concepts": [
            "Customer",
            "Invoice",
        ],

        "result_concept":
            "Customer",

        "measure_concept":
            "Invoice",

        # ==================================================
        # IMPORTANT
        #
        # This deliberately reproduces the wording that
        # caused the original real OpenAI runtime failure.
        #
        # There is NO physical "invoice count" column.
        #
        # SemanticQueryNormalizer must prevent this phrase
        # from reaching ColumnResolver as an attribute.
        # ==================================================

        "measure_attribute":
            "invoice count",

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
            "COUNT",
    }

    agent, neo4j = (
        build_agent(
            structured_response
        )
    )

    try:

        response = (
            agent.ask(
                "How many invoices "
                "does Blue Prism Technologies LE-1001 have?"
            )
        )

        # ==================================================
        # Final response contract
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
        # QueryPlanBuilder dynamically physicalizes:
        #
        # COUNT Invoice entity
        #       ↓
        # Invoice physical table
        #       ↓
        # Neo4j PK metadata
        #       ↓
        # id
        #
        # ResultAnalyzer exposes the deterministic alias.
        # ==================================================

        assert (
            response.data["alias"]
            ==
            "count_id"
        )

        # ==================================================
        # Current verified Golden value
        # ==================================================

        assert (
            response.data["value"]
            == 4
        )

        # ==================================================
        # Business-facing answer
        # ==================================================

        assert (
            response.answer
            ==
            "The count of invoice records "
            "for Blue Prism Technologies LE-1001 is 4."
        )

        # ==================================================
        # Physical database identity must not leak into
        # the business-facing answer.
        # ==================================================

        assert (
            "The id for"
            not in response.answer
        )

    finally:

        neo4j.close()


# ==========================================================
# Provider abstraction / factory wiring
#
# Verify that the assembled Research Agent still accepts
# the provider-neutral ChatModel abstraction.
#
# SemanticQueryNormalizer must NOT couple the agent to:
#
# OpenAI
# Bedrock
# Anthropic
# Azure OpenAI
# etc.
# ==========================================================

def test_factory_accepts_chat_model_abstraction():

    chat_model = (
        MockChatModel(
            structured_response={
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

                "group_by_concepts":
                    [],

                "entities": [
                    {
                        "concept":
                            "Customer",

                        "attribute":
                            "customer name",

                        "value":
                            "Blue Prism",
                    }
                ],

                "aggregation":
                    "SUM",
            }
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

        # ==================================================
        # Factory successfully assembled the complete
        # provider-neutral Research Agent.
        # ==================================================

        assert isinstance(
            agent,
            ResearchAgentOrchestrator,
        )

        # ==================================================
        # Semantic trust boundary is actually wired into
        # the orchestrator.
        # ==================================================

        assert (
            agent
            ._semantic_query_normalizer
            is not None
        )

    finally:

        neo4j.close()

# ==========================================================
# Decision D-001: an ambiguous customer name is clarified,
# never answered with a combined total.
#
# "Blue Prism" matches six customer records owned by three
# different companies (527, 528, 529). The previous expected
# 24000.00 was a cross-company sum and is retired.
# ==========================================================

@pytest.mark.integration
def test_research_agent_ambiguous_customer_name_requests_clarification():

    agent, neo4j = build_agent(
        {
            "intent": "aggregate",
            "concepts": ["Customer", "PaymentReceipt"],
            "result_concept": "Customer",
            "measure_concept": "PaymentReceipt",
            "measure_attribute": "payment amount",
            "group_by_concepts": [],
            "entities": [
                {
                    "concept": "Customer",
                    "attribute": "customer name",
                    "value": "Blue Prism",
                }
            ],
            "aggregation": "SUM",
        }
    )

    try:
        response = agent.ask("How much has Blue Prism paid?")

        assert response.result_type == "clarification"
        assert response.data is None
        assert response.row_count == 0
        assert "more than one record" in response.answer
        assert "24000" not in response.answer
        assert "erp_customer_details" not in response.answer

    finally:
        neo4j.close()
