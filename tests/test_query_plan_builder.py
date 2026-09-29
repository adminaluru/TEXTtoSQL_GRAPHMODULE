from database.neo4j import (
    Neo4jClient,
)

from nlu.models import (
    QuestionAnalysis,
)

from ontology.ontology_service import (
    OntologyService,
)

from planning.column_resolver import (
    ColumnResolver,
)

from planning.graph_planner import (
    GraphPlanner,
)

from planning.grain_resolver import (
    GrainResolver,
)

from planning.query_plan_builder import (
    QueryPlanBuilder,
)

from semantic.models import (
    AggregationIntent,
    SemanticFilter,
    SemanticQuery,
)


# ==========================================================
# GQ001
#
# Attribute aggregation:
#
# "How much has Blue Prism paid?"
#
# SUM(PaymentReceipt.payment amount)
#
# Temporary POC filter behavior:
#
# Customer.name ILIKE '%Blue Prism%'
# ==========================================================

def test_customer_payment_query_plan():

    neo4j = Neo4jClient()

    try:

        ontology = (
            OntologyService(
                neo4j
            )
        )

        # ==================================================
        # Canonical semantic contract
        # ==================================================

        semantic_query = (
            SemanticQuery(
                semantic_version=
                    "1.0",

                query_type=
                    "aggregate",

                concepts=[
                    "Customer",
                    "PaymentReceipt",
                ],

                result_concept=
                    "Customer",

                aggregations=[
                    AggregationIntent(
                        function=
                            "SUM",

                        target_kind=
                            "attribute",

                        concept=
                            "PaymentReceipt",

                        attribute=
                            "payment amount",

                        distinct_requested=
                            False,
                    )
                ],

                filters=[
                    SemanticFilter(
                        concept=
                            "Customer",

                        attribute=
                            "customer name",

                        operator=
                            "=",

                        value=
                            "Blue Prism",
                    )
                ],
            )
        )

        # ==================================================
        # Planning-compatible semantic representation
        # ==================================================

        analysis = (
            QuestionAnalysis(
                intent=
                    "aggregate",

                concepts=[
                    "Customer",
                    "PaymentReceipt",
                ],

                result_concept=
                    "Customer",

                measure_concept=
                    "PaymentReceipt",

                measure_attribute=
                    "payment amount",

                group_by_concepts=
                    [],

                entities=[
                    {
                        "concept":
                            "Customer",

                        "attribute":
                            "customer name",

                        "value":
                            "Blue Prism",
                    }
                ],

                aggregation=
                    "SUM",
            )
        )

        # ==================================================
        # Real deterministic planning stages
        # ==================================================

        graph_plan = (
            GraphPlanner(
                ontology
            )
            .plan(
                analysis
            )
        )

        grain = (
            GrainResolver()
            .resolve(
                analysis=
                    analysis,

                graph_plan=
                    graph_plan,
            )
        )

        columns = (
            ColumnResolver(
                ontology
            )
            .resolve(
                analysis=
                    analysis,

                graph_plan=
                    graph_plan,
            )
        )

        # ==================================================
        # Preconditions
        # ==================================================

        assert (
            columns.measure
            is not None
        )

        assert (
            columns.measure.column_key
            ==
            "public.erp_payment_receipt.payment_amount"
        )

        assert (
            len(
                columns.filters
            )
            == 1
        )

        assert (
            columns.filters[0].operator
            == "ILIKE"
        )

        assert (
            columns.filters[0].value
            == "%Blue Prism%"
        )

        # ==================================================
        # Physical QueryPlan
        # ==================================================

        builder = (
            QueryPlanBuilder(
                ontology
            )
        )

        plan = (
            builder.build(
                semantic_query=
                    semantic_query,

                analysis=
                    analysis,

                graph_plan=
                    graph_plan,

                grain=
                    grain,

                columns=
                    columns,
            )
        )

        # ==================================================
        # Tables
        # ==================================================

        assert (
            "public.erp_customer_details"
            in plan.tables
        )

        assert (
            "public.erp_payment_receipt"
            in plan.tables
        )

        # ==================================================
        # Filter
        # ==================================================

        assert (
            len(
                plan.filters
            )
            == 1
        )

        filter_item = (
            plan.filters[0]
        )

        assert (
            filter_item.table
            ==
            "public.erp_customer_details"
        )

        assert (
            filter_item.column
            == "name"
        )

        assert (
            filter_item.operator
            == "ILIKE"
        )

        assert (
            filter_item.value
            == "%Blue Prism%"
        )

        # ==================================================
        # Aggregation
        # ==================================================

        assert (
            len(
                plan.aggregations
            )
            == 1
        )

        aggregation = (
            plan.aggregations[0]
        )

        assert (
            aggregation.function
            == "SUM"
        )

        assert (
            aggregation.table
            ==
            "public.erp_payment_receipt"
        )

        assert (
            aggregation.column
            == "payment_amount"
        )

        assert (
            aggregation.alias
            == "sum_payment_amount"
        )

        # ==================================================
        # Aggregate query -> no list LIMIT
        # ==================================================

        assert (
            plan.limit
            is None
        )

    finally:

        neo4j.close()


# ==========================================================
# GQ002
#
# Entity aggregation:
#
# "How many invoices does Blue Prism have?"
#
# COUNT(Invoice entity)
#
# Critical production behavior:
#
# SemanticQuery contains:
#
#     COUNT
#     target_kind = entity
#     concept = Invoice
#     attribute = None
#
# ColumnResolver MUST NOT resolve "invoice count".
#
# QueryPlanBuilder resolves Invoice identity through
# Neo4j primary-key metadata.
#
# Temporary POC filter behavior:
#
# Customer.name ILIKE '%Blue Prism%'
# ==========================================================

def test_customer_invoice_entity_count_query_plan():

    neo4j = Neo4jClient()

    try:

        ontology = (
            OntologyService(
                neo4j
            )
        )

        # ==================================================
        # Canonical semantic contract
        # ==================================================

        semantic_query = (
            SemanticQuery(
                semantic_version=
                    "1.0",

                query_type=
                    "aggregate",

                concepts=[
                    "Customer",
                    "Invoice",
                ],

                result_concept=
                    "Customer",

                aggregations=[
                    AggregationIntent(
                        function=
                            "COUNT",

                        target_kind=
                            "entity",

                        concept=
                            "Invoice",

                        attribute=
                            None,

                        distinct_requested=
                            False,
                    )
                ],

                filters=[
                    SemanticFilter(
                        concept=
                            "Customer",

                        attribute=
                            "customer name",

                        operator=
                            "=",

                        value=
                            "Blue Prism",
                    )
                ],
            )
        )

        # ==================================================
        # Planning-compatible analysis
        #
        # IMPORTANT:
        #
        # measure_attribute is None.
        #
        # We intentionally do NOT carry:
        #
        # "invoice count"
        #
        # into ColumnResolver.
        # ==================================================

        analysis = (
            QuestionAnalysis(
                intent=
                    "count",

                concepts=[
                    "Customer",
                    "Invoice",
                ],

                result_concept=
                    "Customer",

                measure_concept=
                    "Invoice",

                measure_attribute=
                    None,

                group_by_concepts=
                    [],

                entities=[
                    {
                        "concept":
                            "Customer",

                        "attribute":
                            "customer name",

                        "value":
                            "Blue Prism",
                    }
                ],

                aggregation=
                    "COUNT",
            )
        )

        # ==================================================
        # Graph planning
        # ==================================================

        graph_plan = (
            GraphPlanner(
                ontology
            )
            .plan(
                analysis
            )
        )

        # ==================================================
        # Grain
        # ==================================================

        grain = (
            GrainResolver()
            .resolve(
                analysis=
                    analysis,

                graph_plan=
                    graph_plan,
            )
        )

        # ==================================================
        # Column resolution
        #
        # Only Customer filter should resolve.
        #
        # COUNT(entity Invoice) deliberately has no
        # physical measure column here.
        # ==================================================

        columns = (
            ColumnResolver(
                ontology
            )
            .resolve(
                analysis=
                    analysis,

                graph_plan=
                    graph_plan,
            )
        )

        # ==================================================
        # Critical semantic-boundary assertions
        # ==================================================

        assert (
            columns.measure
            is None
        )

        assert (
            len(
                columns.filters
            )
            == 1
        )

        assert (
            columns.filters[0]
            .column_key
            ==
            "public.erp_customer_details.name"
        )

        assert (
            columns.filters[0].operator
            == "ILIKE"
        )

        assert (
            columns.filters[0].value
            == "%Blue Prism%"
        )

        # ==================================================
        # QueryPlanBuilder physicalizes entity COUNT
        # ==================================================

        builder = (
            QueryPlanBuilder(
                ontology
            )
        )

        plan = (
            builder.build(
                semantic_query=
                    semantic_query,

                analysis=
                    analysis,

                graph_plan=
                    graph_plan,

                grain=
                    grain,

                columns=
                    columns,
            )
        )

        # ==================================================
        # Tables
        # ==================================================

        assert (
            "public.erp_customer_details"
            in plan.tables
        )

        assert (
            "public.erp_invoice_details"
            in plan.tables
        )

        # ==================================================
        # Customer filter
        # ==================================================

        assert (
            len(
                plan.filters
            )
            == 1
        )

        filter_item = (
            plan.filters[0]
        )

        assert (
            filter_item.table
            ==
            "public.erp_customer_details"
        )

        assert (
            filter_item.column
            == "name"
        )

        assert (
            filter_item.operator
            == "ILIKE"
        )

        assert (
            filter_item.value
            == "%Blue Prism%"
        )

        # ==================================================
        # Entity COUNT
        #
        # QueryPlanBuilder should dynamically resolve:
        #
        # Invoice
        #      ↓
        # public.erp_invoice_details
        #      ↓
        # Neo4j PK metadata
        #      ↓
        # id
        #
        # No hard-coded Invoice -> id mapping.
        # ==================================================

        assert (
            len(
                plan.aggregations
            )
            == 1
        )

        aggregation = (
            plan.aggregations[0]
        )

        assert (
            aggregation.function
            == "COUNT"
        )

        assert (
            aggregation.table
            ==
            "public.erp_invoice_details"
        )

        assert (
            aggregation.column
            == "id"
        )

        assert (
            aggregation.alias
            == "count_id"
        )

        # ==================================================
        # Aggregate query -> no list LIMIT
        # ==================================================

        assert (
            plan.limit
            is None
        )

    finally:

        neo4j.close()