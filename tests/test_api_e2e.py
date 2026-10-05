from decimal import Decimal

from fastapi.testclient import (
    TestClient,
)

from ai.providers.mock_chat import (
    MockChatModel,
)

from agent.factory import (
    build_research_agent,
)

from api.application import (
    create_app,
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

def build_e2e_agent(
    structured_response: dict,
):
    """
    Build the complete Research Agent for API E2E testing.

    Only the external LLM/provider response is mocked.

    Everything after the ChatModel boundary is real:

    - QuestionAnalyzer
    - SemanticQueryNormalizer
    - Neo4j ontology
    - GraphPlanner
    - GrainResolver
    - ColumnResolver
    - QueryPlanBuilder
    - SQLCompiler
    - SQLValidator
    - SQLGuardrails
    - PostgreSQLExecutor
    - ResultAnalyzer
    - AnswerService
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

    return (
        agent,
        neo4j,
    )


# ==========================================================
# GQ001
#
# FastAPI
#   ↓
# SUM(attribute)
#   ↓
# PaymentReceipt.payment amount
#
# Temporary POC entity matching:
#
# Customer.name ILIKE '%Blue Prism%'
#
# Verified Golden Data:
#
# matching customers = 6
# payment total      = 24000.00
# ==========================================================

def test_api_e2e_total_payment_for_customer():

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

                # Deliberately use realistic LLM wording.
                "attribute":
                    "customer name",

                "value":
                    "Blue Prism",
            }
        ],

        "aggregation":
            "SUM",
    }

    agent, neo4j = (
        build_e2e_agent(
            structured_response
        )
    )

    try:

        app = (
            create_app(
                agent
            )
        )

        with TestClient(
            app
        ) as client:

            response = (
                client.post(
                    "/ask",

                    json={
                        "question":
                            "How much has "
                            "Blue Prism paid?"
                    },
                )
            )

        # ==================================================
        # HTTP contract
        # ==================================================

        assert (
            response.status_code
            == 200
        )

        body = (
            response.json()
        )

        # ==================================================
        # Result contract
        # ==================================================

        assert (
            body["result_type"]
            == "scalar"
        )

        assert (
            body["row_count"]
            == 1
        )

        assert (
            body["warnings"]
            == []
        )

        assert (
            body["data"]
            is not None
        )

        # ==================================================
        # Physical aggregation
        #
        # SUM PaymentReceipt.payment amount
        #       ↓
        # SUM(payment_amount)
        # ==================================================

        assert (
            body["data"]["alias"]
            ==
            "sum_payment_amount"
        )

        # ==================================================
        # Current verified Golden value
        #
        # Decimal(str(...)) keeps this assertion stable
        # whether FastAPI serializes the decimal as:
        #
        # 24000.0
        # "24000.00"
        # etc.
        # ==================================================

        assert (
            Decimal(
                str(
                    body[
                        "data"
                    ][
                        "value"
                    ]
                )
            )
            ==
            Decimal(
                "24000.00"
            )
        )

        # ==================================================
        # Business-facing answer
        # ==================================================

        assert (
            body["answer"]
            ==
            "The total payment amount "
            "for Blue Prism is 24000.00."
        )

        assert (
            "Blue Prism"
            in body["answer"]
        )

        assert (
            "payment amount"
            in body["answer"]
        )

    finally:

        neo4j.close()


# ==========================================================
# GQ002
#
# FastAPI
#   ↓
# raw LLM:
#
#     measure_attribute = "invoice count"
#
#   ↓
# SemanticQueryNormalizer
#   ↓
#
# COUNT entity Invoice
# attribute = None
#
#   ↓
# ColumnResolver does NOT resolve "invoice count"
#   ↓
# QueryPlanBuilder resolves Invoice identity from Neo4j
#   ↓
# COUNT(id)
#
# Temporary POC entity matching:
#
# Customer.name ILIKE '%Blue Prism%'
#
# Verified Golden Data:
#
# matching customers = 6
# invoice count      = 24
# ==========================================================

def test_api_e2e_count_invoices_for_customer():

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
        # This reproduces the actual wording returned by
        # real OpenAI that originally caused:
        #
        # Ambiguous column resolution for "invoice count"
        #
        # SemanticQueryNormalizer must absorb this phrase.
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
                    "Blue Prism",
            }
        ],

        "aggregation":
            "COUNT",
    }

    agent, neo4j = (
        build_e2e_agent(
            structured_response
        )
    )

    try:

        app = (
            create_app(
                agent
            )
        )

        with TestClient(
            app
        ) as client:

            response = (
                client.post(
                    "/ask",

                    json={
                        "question":
                            "How many invoices "
                            "does Blue Prism have?"
                    },
                )
            )

        # ==================================================
        # HTTP contract
        # ==================================================

        assert (
            response.status_code
            == 200
        )

        body = (
            response.json()
        )

        # ==================================================
        # Result contract
        # ==================================================

        assert (
            body["result_type"]
            == "scalar"
        )

        assert (
            body["row_count"]
            == 1
        )

        assert (
            body["warnings"]
            == []
        )

        assert (
            body["data"]
            is not None
        )

        # ==================================================
        # Physical entity-count contract
        #
        # Invoice
        #    ↓
        # Neo4j table
        #    ↓
        # Neo4j PK metadata
        #    ↓
        # id
        #    ↓
        # COUNT(id)
        # ==================================================

        assert (
            body["data"]["alias"]
            ==
            "count_id"
        )

        # ==================================================
        # Current verified Golden value
        # ==================================================

        assert (
            body["data"]["value"]
            == 24
        )

        # ==================================================
        # Business-facing answer
        # ==================================================

        assert (
            body["answer"]
            ==
            "The count of invoice records "
            "for Blue Prism is 24."
        )

        # Physical identity must not leak into the answer.

        assert (
            "The id for"
            not in body["answer"]
        )

    finally:

        neo4j.close()


# ==========================================================
# Health
#
# Health itself should not execute semantic planning.
# ==========================================================

def test_api_e2e_health():

    structured_response = {
        "intent":
            "count",

        "concepts": [
            "Invoice",
        ],

        "result_concept":
            "Invoice",

        "measure_concept":
            "Invoice",

        "measure_attribute":
            None,

        "group_by_concepts":
            [],

        "entities":
            [],

        "aggregation":
            "COUNT",
    }

    agent, neo4j = (
        build_e2e_agent(
            structured_response
        )
    )

    try:

        app = (
            create_app(
                agent
            )
        )

        with TestClient(
            app
        ) as client:

            response = (
                client.get(
                    "/health"
                )
            )

        assert (
            response.status_code
            == 200
        )

        assert (
            response.json()
            ==
            {
                "status": "ok"
            }
        )

    finally:

        neo4j.close()

# ==========================================================
# Cached ontology + pooled PostgreSQL
#
# Same GQ001 question, run through the production wiring:
# a TTLCache-backed ontology and a psycopg connection pool.
# Repeated requests must return the identical verified
# answer while the second request skips Neo4j lookups.
# ==========================================================

def test_api_e2e_cached_ontology_and_pooled_postgres():

    import time

    from psycopg_pool import ConnectionPool

    from config import settings
    from ontology.cache import TTLCache

    structured_response = {
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

    neo4j = Neo4jClient()

    lookups = []

    original_read = neo4j.execute_read

    def counting_read(query, parameters=None):
        lookups.append(1)
        return original_read(query, parameters)

    neo4j.execute_read = counting_read

    pool = ConnectionPool(
        settings.postgres_dsn,
        min_size=1,
        max_size=2,
        open=True,
    )

    try:

        agent = build_research_agent(
            chat_model=MockChatModel(
                structured_response=structured_response
            ),
            neo4j_client=neo4j,
            postgres_executor=PostgreSQLExecutor(
                statement_timeout_ms=5000,
                max_rows=100,
                pool=pool,
            ),
            ontology_cache=TTLCache(ttl_seconds=300),
        )

        with TestClient(create_app(agent)) as client:

            values = []
            durations = []
            lookup_counts = []

            for _ in range(3):

                before = len(lookups)
                started = time.perf_counter()

                response = client.post(
                    "/ask",
                    json={"question": "How much has Blue Prism paid?"},
                )

                durations.append(time.perf_counter() - started)
                lookup_counts.append(len(lookups) - before)

                assert response.status_code == 200
                values.append(
                    Decimal(str(response.json()["data"]["value"]))
                )

        assert values == [Decimal("24000.00")] * 3

        # First request populates the cache; later requests
        # make no Neo4j round trips at all.
        assert lookup_counts[0] > 0
        assert lookup_counts[1:] == [0, 0]

        # Read-only/timeout settings must not leak into
        # pooled connections.
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT current_setting('transaction_read_only'), "
                "current_setting('statement_timeout')"
            ).fetchone()

        assert row[0] == "off"
        assert row[1] != "5s"

        print(
            "cached e2e durations (s):",
            [round(value, 3) for value in durations],
            "neo4j lookups:",
            lookup_counts,
        )

    finally:

        pool.close()
        neo4j.close()


# ==========================================================
# Fan-out protection
#
# SUM(invoice.amount) joined to invoice line items repeats
# every invoice once per item. Verified on Golden Data:
#
#   naive joined SUM = 297000.00
#   correct total    =  99000.00
#
# The query must be refused (clarification) rather than
# answered wrongly.
# ==========================================================

def test_api_e2e_blocks_sum_inflated_by_line_items():

    agent, neo4j = build_e2e_agent(
        {
            "intent": "aggregate",
            "concepts": ["Customer", "Invoice", "InvoiceItem"],
            "result_concept": "Customer",
            "measure_concept": "Invoice",
            "measure_attribute": "invoice amount",
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

        with TestClient(create_app(agent)) as client:

            response = client.post(
                "/ask",
                json={
                    "question":
                        "What is the total invoice amount for "
                        "Blue Prism invoice line items?"
                },
            )

        # The user gets a clarifying question in business
        # terms, never the inflated total or table names.
        assert response.status_code == 200

        body = response.json()

        assert body["result_type"] == "clarification"
        assert body["data"] is None
        assert "297000" not in response.text
        assert "erp_" not in body["answer"]
        assert "Invoice" in body["answer"]
        assert "InvoiceItem" in body["answer"]

    finally:

        neo4j.close()
