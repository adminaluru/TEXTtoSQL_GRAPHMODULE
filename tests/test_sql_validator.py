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

from sql_compiler.compiler import (
    DeterministicSQLCompiler,
)

from sql_validator.validator import (
    SQLValidator,
)


# ==========================================================
# GQ001 SQL validation
#
# Verify the current physical query produced for:
#
# SUM(PaymentReceipt.payment amount)
#
# with temporary partial-name matching:
#
# Customer.name ILIKE '%Blue Prism%'
#
# passes structural SQL validation.
# ==========================================================

def test_customer_payment_sql_validation():

    neo4j = Neo4jClient()

    try:

        # ==================================================
        # 1. Canonical SemanticQuery
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
                            "name",

                        operator=
                            "=",

                        value=
                            "Blue Prism",
                    )
                ],
            )
        )

        # ==================================================
        # 2. Planning-compatible QuestionAnalysis
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
                            "name",

                        "value":
                            "Blue Prism",
                    }
                ],

                aggregation=
                    "SUM",
            )
        )

        # ==================================================
        # 3. Ontology
        # ==================================================

        ontology = (
            OntologyService(
                neo4j
            )
        )

        # ==================================================
        # 4. GraphPlan
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
        # 5. Grain
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
        # 6. Column resolution
        # ==================================================

        column_resolution = (
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
        # Temporary POC filter semantics
        # ==================================================

        assert (
            len(
                column_resolution.filters
            )
            == 1
        )

        assert (
            column_resolution
            .filters[0]
            .operator
            == "ILIKE"
        )

        assert (
            column_resolution
            .filters[0]
            .value
            == "%Blue Prism%"
        )

        # ==================================================
        # 7. QueryPlan
        #
        # Current production contract:
        #
        # QueryPlanBuilder(ontology)
        #
        # + canonical SemanticQuery
        # ==================================================

        query_plan = (
            QueryPlanBuilder(
                ontology
            )
            .build(
                semantic_query=
                    semantic_query,

                analysis=
                    analysis,

                graph_plan=
                    graph_plan,

                grain=
                    grain,

                columns=
                    column_resolution,
            )
        )

        # ==================================================
        # 8. Deterministic SQL compilation
        # ==================================================

        compiled = (
            DeterministicSQLCompiler()
            .compile(
                query_plan
            )
        )

        # ==================================================
        # 9. Parameterization assertions
        # ==================================================

        assert (
            "Blue Prism"
            not in compiled.sql
        )

        assert (
            compiled.parameters
            == [
                "%Blue Prism%"
            ]
        )

        assert (
            "ILIKE"
            in compiled.sql
        )

        # ==================================================
        # 10. Structural SQL validation
        # ==================================================

        validation = (
            SQLValidator()
            .validate(
                compiled=
                    compiled,

                plan=
                    query_plan,
            )
        )

        assert (
            validation.valid
            is True
        )

        assert (
            validation.errors
            == []
        )

    finally:

        neo4j.close()