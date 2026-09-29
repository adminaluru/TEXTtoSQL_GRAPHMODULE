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


# ==========================================================
# GQ001 SQL compilation
#
# How much has the matching customer group paid?
#
# Temporary POC matching:
#
# Customer.name ILIKE '%Blue Prism%'
# ==========================================================

def test_customer_payment_sql_compilation():

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
        # 2. Planning-compatible analysis
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
        # 4. Graph planning
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

        # Temporary Golden Data policy must be visible
        # before QueryPlan construction.

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
        # 9. Expected SQL
        #
        # Business runtime value must remain parameterized.
        # ==================================================

        expected_sql = (
            "SELECT "
            "SUM(t1.payment_amount) "
            "AS sum_payment_amount\n"
            "FROM "
            "public.erp_customer_details "
            "AS t0\n"
            "INNER JOIN "
            "public.erp_payment_receipt "
            "AS t1\n"
            "    ON "
            "t1.customer_id = t0.id\n"
            "WHERE "
            "t0.name ILIKE %s"
        )

        assert (
            compiled.sql
            == expected_sql
        )

        assert (
            compiled.parameters
            == [
                "%Blue Prism%"
            ]
        )

        # ==================================================
        # Critical SQL-injection / parameterization safety
        #
        # Runtime business values must NOT be interpolated
        # directly into SQL.
        # ==================================================

        assert (
            "Blue Prism"
            not in compiled.sql
        )

        # ==================================================
        # Explicit temporary filter semantics
        # ==================================================

        assert (
            "ILIKE"
            in compiled.sql
        )

        assert (
            "payment_amount"
            in compiled.sql
        )

        assert (
            "SUM("
            in compiled.sql
        )

    finally:

        neo4j.close()