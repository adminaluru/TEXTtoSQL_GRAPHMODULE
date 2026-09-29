from database.neo4j import (
    Neo4jClient,
)

from nlu.models import (
    QuestionAnalysis,
)

from ontology.concept_catalog import (
    ConceptCatalogService,
)

from semantic.normalizer import (
    SemanticQueryNormalizer,
)


def build_normalizer():
    neo4j = Neo4jClient()

    normalizer = (
        SemanticQueryNormalizer(
            ConceptCatalogService(
                neo4j
            )
        )
    )

    return normalizer, neo4j


# ==========================================================
# GQ001
# SUM attribute
# ==========================================================

def test_sum_payment_amount_normalizes():

    normalizer, neo4j = (
        build_normalizer()
    )

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

            measure_attribute=
                "payment amount",

            group_by_concepts=[],

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

            aggregation="SUM",
        )

        result = (
            normalizer.normalize(
                analysis
            )
        )

        assert (
            result.status
            == "ready"
        )

        query = (
            result.query
        )

        assert (
            query
            is not None
        )

        assert (
            query.aggregations[0].function
            == "SUM"
        )

        assert (
            query.aggregations[0]
            .target_kind
            == "attribute"
        )

        assert (
            query.aggregations[0].concept
            == "PaymentReceipt"
        )

        assert (
            query.aggregations[0].attribute
            == "payment amount"
        )

        # Scalar aggregate contains no grouping.
        assert (
            query.groupings
            == []
        )

    finally:

        neo4j.close()


# ==========================================================
# GQ002
# COUNT entity
# ==========================================================

def test_invoice_count_phrase_becomes_entity_count():

    normalizer, neo4j = (
        build_normalizer()
    )

    try:

        analysis = QuestionAnalysis(
            intent="count",

            concepts=[
                "Customer",
                "Invoice",
            ],

            result_concept=
                "Customer",

            measure_concept=
                "Invoice",

            measure_attribute=
                "invoice count",

            group_by_concepts=[],

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

            aggregation="COUNT",
        )

        result = (
            normalizer.normalize(
                analysis
            )
        )

        assert (
            result.status
            == "ready"
        )

        assert (
            result.query
            is not None
        )

        aggregation = (
            result.query
            .aggregations[0]
        )

        assert (
            aggregation.function
            == "COUNT"
        )

        assert (
            aggregation.target_kind
            == "entity"
        )

        assert (
            aggregation.concept
            == "Invoice"
        )

        assert (
            aggregation.attribute
            is None
        )

        assert (
            result.query.groupings
            == []
        )

    finally:

        neo4j.close()


def test_count_with_no_measure_attribute_is_valid():

    normalizer, neo4j = (
        build_normalizer()
    )

    try:

        analysis = QuestionAnalysis(
            intent="count",

            concepts=[
                "Invoice",
            ],

            result_concept=
                "Invoice",

            measure_concept=
                "Invoice",

            measure_attribute=
                None,

            group_by_concepts=[],

            entities=[],

            aggregation="COUNT",
        )

        result = (
            normalizer.normalize(
                analysis
            )
        )

        assert (
            result.status
            == "ready"
        )

        assert (
            result.query
            is not None
        )

        assert (
            result.query
            .aggregations[0]
            .target_kind
            == "entity"
        )

        assert (
            result.query.groupings
            == []
        )

    finally:

        neo4j.close()


# ==========================================================
# GQ004
#
# Entity GROUP BY
#
# "What is the total payment amount by customer?"
#
# Raw QuestionAnalysis:
#
#     group_by_concepts=["Customer"]
#
# Canonical semantic meaning:
#
#     GroupingIntent(
#         target_kind="entity",
#         concept="Customer",
#         attribute=None,
#     )
#
# IMPORTANT:
#
# Semantic normalization must NOT decide:
#
#     Customer.id
#     Customer.name
#
# Those are physical planning decisions.
# ==========================================================

def test_entity_group_by_normalizes():

    normalizer, neo4j = (
        build_normalizer()
    )

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

            measure_attribute=
                "payment amount",

            group_by_concepts=[
                "Customer"
            ],

            entities=[],

            aggregation="SUM",
        )

        result = (
            normalizer.normalize(
                analysis
            )
        )

        # ==================================================
        # Semantic trust boundary accepts the query.
        # ==================================================

        assert (
            result.status
            == "ready"
        )

        assert (
            result.reason_code
            is None
        )

        assert (
            result.query
            is not None
        )

        query = (
            result.query
        )

        # ==================================================
        # Aggregation remains unchanged.
        # ==================================================

        assert (
            len(
                query.aggregations
            )
            == 1
        )

        aggregation = (
            query.aggregations[0]
        )

        assert (
            aggregation.function
            == "SUM"
        )

        assert (
            aggregation.target_kind
            == "attribute"
        )

        assert (
            aggregation.concept
            == "PaymentReceipt"
        )

        assert (
            aggregation.attribute
            == "payment amount"
        )

        # ==================================================
        # Canonical entity grouping
        # ==================================================

        assert (
            len(
                query.groupings
            )
            == 1
        )

        grouping = (
            query.groupings[0]
        )

        assert (
            grouping.target_kind
            == "entity"
        )

        assert (
            grouping.concept
            == "Customer"
        )

        # No physical grouping column is invented here.
        assert (
            grouping.attribute
            is None
        )

    finally:

        neo4j.close()


# ==========================================================
# GROUP BY fail-closed validation
#
# A grouping concept must already belong to the
# canonical query concepts.
# ==========================================================

def test_group_by_concept_not_in_query_is_invalid():

    normalizer, neo4j = (
        build_normalizer()
    )

    try:

        analysis = QuestionAnalysis(
            intent="aggregate",

            concepts=[
                "PaymentReceipt",
            ],

            result_concept=
                "PaymentReceipt",

            measure_concept=
                "PaymentReceipt",

            measure_attribute=
                "payment amount",

            # Customer is deliberately absent from
            # analysis.concepts.
            group_by_concepts=[
                "Customer"
            ],

            entities=[],

            aggregation="SUM",
        )

        result = (
            normalizer.normalize(
                analysis
            )
        )

        assert (
            result.status
            == "invalid"
        )

        assert (
            result.reason_code
            ==
            "GROUP_BY_CONCEPT_NOT_IN_QUERY"
        )

        assert (
            result.query
            is None
        )

    finally:

        neo4j.close()


# ==========================================================
# Fail-closed cases
# ==========================================================

def test_sum_without_attribute_is_invalid():

    normalizer, neo4j = (
        build_normalizer()
    )

    try:

        analysis = QuestionAnalysis(
            intent="aggregate",

            concepts=[
                "Invoice",
            ],

            result_concept=
                "Invoice",

            measure_concept=
                "Invoice",

            measure_attribute=
                None,

            group_by_concepts=[],

            entities=[],

            aggregation="SUM",
        )

        result = (
            normalizer.normalize(
                analysis
            )
        )

        assert (
            result.status
            == "invalid"
        )

        assert (
            result.reason_code
            ==
            "MISSING_ATTRIBUTE_TARGET"
        )

    finally:

        neo4j.close()


def test_attribute_count_is_not_silently_guessed():

    normalizer, neo4j = (
        build_normalizer()
    )

    try:

        analysis = QuestionAnalysis(
            intent="count",

            concepts=[
                "Invoice",
            ],

            result_concept=
                "Invoice",

            measure_concept=
                "Invoice",

            measure_attribute=
                "status",

            group_by_concepts=[],

            entities=[],

            aggregation="COUNT",
        )

        result = (
            normalizer.normalize(
                analysis
            )
        )

        assert (
            result.status
            == "unsupported"
        )

        assert (
            result.reason_code
            ==
            "UNSUPPORTED_ATTRIBUTE_COUNT"
        )

    finally:

        neo4j.close()


def test_gq003_list_query_normalizes_for_default_projection():

    normalizer, neo4j = (
        build_normalizer()
    )

    try:

        analysis = QuestionAnalysis(
            intent="list",

            concepts=[
                "Customer",
                "Invoice",
            ],

            result_concept=
                "Invoice",

            measure_concept=
                None,

            measure_attribute=
                None,

            group_by_concepts=[],

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
                None,
        )

        result = (
            normalizer.normalize(
                analysis
            )
        )

        assert (
            result.status
            == "ready"
        )

        assert (
            result.reason_code
            is None
        )

        assert (
            result.query
            is not None
        )

        query = (
            result.query
        )

        assert (
            query.query_type
            == "list"
        )

        assert (
            query.result_concept
            == "Invoice"
        )

        assert (
            query.aggregations
            == []
        )

        assert (
            query.groupings
            == []
        )

        assert (
            len(
                query.filters
            )
            == 1
        )

        assert (
            query.filters[0].concept
            == "Customer"
        )

        assert (
            query.filters[0].attribute
            == "customer name"
        )

        assert (
            query.filters[0].value
            == "Blue Prism"
        )

    finally:

        neo4j.close()

def test_unknown_concept_is_invalid():

    normalizer, neo4j = (
        build_normalizer()
    )

    try:

        analysis = QuestionAnalysis(
            intent="count",

            concepts=[
                "MadeUpConcept",
            ],

            result_concept=
                "MadeUpConcept",

            measure_concept=
                "MadeUpConcept",

            measure_attribute=
                None,

            group_by_concepts=[],

            entities=[],

            aggregation="COUNT",
        )

        result = (
            normalizer.normalize(
                analysis
            )
        )

        assert (
            result.status
            == "invalid"
        )

        assert (
            result.reason_code
            ==
            "UNKNOWN_CONCEPT"
        )

    finally:

        neo4j.close()