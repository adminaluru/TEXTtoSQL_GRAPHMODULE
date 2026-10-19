import pytest

pytestmark = pytest.mark.integration

from database.neo4j import Neo4jClient

from nlu.models import QuestionAnalysis

from ontology.ontology_service import OntologyService

from planning.graph_planner import GraphPlanner
from planning.grain_resolver import GrainResolver


def test_dynamic_customer_payment_grain():

    neo4j = Neo4jClient()

    try:

        analysis = QuestionAnalysis(
            intent="aggregate",

            concepts=[
                "Customer",
                "PaymentReceipt",
            ],

            result_concept=
                "Customer",

            measure_concept=
                "PaymentReceipt",

            group_by_concepts=[],

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

        ontology = OntologyService(
            neo4j
        )

        graph_planner = GraphPlanner(
            ontology
        )

        graph_plan = graph_planner.plan(
            analysis
        )

        resolver = GrainResolver()

        grain = resolver.resolve(
            analysis=analysis,
            graph_plan=graph_plan,
        )

        assert grain.result_concepts == [
            "Customer"
        ]

        assert grain.result_table_keys == [
            "public.erp_customer_details"
        ]

        assert (
            grain.working_concept
            == "PaymentReceipt"
        )

        assert (
            grain.working_table_key
            == "public.erp_payment_receipt"
        )

        assert (
            grain.measure_concept
            == "PaymentReceipt"
        )

        assert (
            grain.measure_table_key
            == "public.erp_payment_receipt"
        )

        assert (
            grain.aggregation
            == "SUM"
        )

    finally:

        neo4j.close()