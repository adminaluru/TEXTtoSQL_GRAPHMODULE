import pytest

from ai.providers.mock_chat import (
    MockChatModel,
)

from database.neo4j import (
    Neo4jClient,
)

from nlu.question_analyzer import (
    QuestionAnalyzer,
)

from ontology.concept_catalog import (
    ConceptCatalogService,
)

pytestmark = pytest.mark.integration


def test_dynamic_ontology_catalog_is_loaded():

    neo4j = Neo4jClient()

    try:

        catalog = (
            ConceptCatalogService(
                neo4j
            )
            .list_business_concepts()
        )

        assert len(catalog) == 40

        concepts = {
            item.concept
            for item in catalog
        }

        assert len(concepts) == 40

    finally:

        neo4j.close()


def test_question_analyzer_rejects_unknown_concept():

    neo4j = Neo4jClient()

    try:

        model = MockChatModel(
            structured_response={
                "intent":
                    "aggregate",

                "concepts": [
                    "DefinitelyNotARealConcept",
                ],

                "result_concept":
                    "DefinitelyNotARealConcept",

                "measure_concept":
                    None,

                "measure_attribute":
                    None,

                "group_by_concepts":
                    [],

                "entities":
                    [],

                "aggregation":
                    None,
            }
        )

        analyzer = QuestionAnalyzer(
            chat_model=model,

            concept_catalog=
                ConceptCatalogService(
                    neo4j
                ),
        )

        with pytest.raises(
            ValueError,
            match=(
                "does not exist in the "
                "Neo4j ontology"
            ),
        ):

            analyzer.analyze(
                "Test unknown concept"
            )

    finally:

        neo4j.close()


def test_question_analyzer_canonicalizes_concept_case():

    neo4j = Neo4jClient()

    try:

        model = MockChatModel(
            structured_response={
                "intent":
                    "aggregate",

                "concepts": [
                    "customer",
                    "paymentreceipt",
                ],

                "result_concept":
                    "customer",

                "measure_concept":
                    "paymentreceipt",

                "measure_attribute":
                    "payment amount",

                "group_by_concepts":
                    [],

                "entities": [
                    {
                        "concept":
                            "customer",

                        "attribute":
                            "name",

                        "value":
                            "Example Company",
                    }
                ],

                "aggregation":
                    "SUM",
            }
        )

        analyzer = QuestionAnalyzer(
            chat_model=model,

            concept_catalog=
                ConceptCatalogService(
                    neo4j
                ),
        )

        result = analyzer.analyze(
            "Test canonicalization"
        )

        assert result.concepts == [
            "Customer",
            "PaymentReceipt",
        ]

        assert (
            result.result_concept
            == "Customer"
        )

        assert (
            result.measure_concept
            == "PaymentReceipt"
        )

        assert (
            result.entities[0].concept
            == "Customer"
        )

    finally:

        neo4j.close()