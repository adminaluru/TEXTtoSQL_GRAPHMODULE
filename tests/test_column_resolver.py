import pytest

pytestmark = pytest.mark.integration

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


# ==========================================================
# Business-language resolution
#
# "customer name"
#     ↓
# public.erp_customer_details.name
#
# Temporary POC matching policy:
#
# name-like filters use:
#
#     ILIKE '%value%'
# ==========================================================

def test_customer_name_business_phrase_resolves():

    neo4j = Neo4jClient()

    try:

        ontology = (
            OntologyService(
                neo4j
            )
        )

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

        graph_plan = (
            GraphPlanner(
                ontology
            )
            .plan(
                analysis
            )
        )

        result = (
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

        assert (
            len(
                result.filters
            )
            == 1
        )

        customer_filter = (
            result.filters[0]
        )

        assert (
            customer_filter.column_key
            ==
            "public.erp_customer_details.name"
        )

        # ==================================================
        # Temporary POC name matching
        # ==================================================

        assert (
            customer_filter.operator
            == "ILIKE"
        )

        assert (
            customer_filter.value
            == "%Blue Prism%"
        )

        assert (
            result.measure
            is not None
        )

        assert (
            result.measure.column_key
            ==
            "public.erp_payment_receipt.payment_amount"
        )

    finally:

        neo4j.close()


# ==========================================================
# Dynamic column resolution
#
# Filter:
#
# Customer.name
#
# Measure:
#
# PaymentReceipt.payment amount
# ==========================================================

def test_dynamic_customer_payment_columns():

    neo4j = Neo4jClient()

    try:

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
                            "name",

                        "value":
                            "Blue Prism",
                    }
                ],

                aggregation=
                    "SUM",
            )
        )

        ontology = (
            OntologyService(
                neo4j
            )
        )

        graph_plan = (
            GraphPlanner(
                ontology
            )
            .plan(
                analysis
            )
        )

        resolution = (
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
        # Customer.name
        # ==================================================

        assert (
            len(
                resolution.filters
            )
            == 1
        )

        customer_filter = (
            resolution.filters[0]
        )

        assert (
            customer_filter.column_key
            ==
            "public.erp_customer_details.name"
        )

        assert (
            customer_filter.operator
            == "ILIKE"
        )

        assert (
            customer_filter.value
            == "%Blue Prism%"
        )

        # ==================================================
        # PaymentReceipt.payment amount
        # ==================================================

        assert (
            resolution.measure
            is not None
        )

        assert (
            resolution.measure.column_key
            ==
            "public.erp_payment_receipt.payment_amount"
        )

        assert (
            resolution.measure.concept
            ==
            "PaymentReceipt"
        )

    finally:

        neo4j.close()