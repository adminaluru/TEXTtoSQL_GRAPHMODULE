import pytest

pytestmark = pytest.mark.integration

from database.neo4j import Neo4jClient
from nlu.models import QuestionAnalysis
from ontology.ontology_service import OntologyService
from planning.graph_planner import GraphPlanner


def test_customer_payment_graph_plan():

    neo4j = Neo4jClient()

    try:

        ontology = OntologyService(
            neo4j
        )

        planner = GraphPlanner(
            ontology
        )

        analysis = QuestionAnalysis(
            intent="aggregate",
            concepts=[
                "Customer",
                "PaymentReceipt",
            ],
            entities=[
                {
                    "concept": "Customer",
                    "attribute": "name",
                    "value": "Blue Prism",
                }
            ],
            aggregation="SUM",
        )

        plan = planner.plan(
            analysis
        )

        assert plan.concepts == [
            "Customer",
            "PaymentReceipt",
        ]

        assert len(
            plan.tables
        ) == 2

        assert len(
            plan.semantic_relationships
        ) == 1

        semantic = (
            plan.semantic_relationships[0]
        )

        assert (
            semantic.relationship_type
            == "MAKES_PAYMENT"
        )

        assert (
            semantic.validation_status
            == "confirmed"
        )

        assert len(
            plan.physical_paths
        ) == 1

    finally:

        neo4j.close()