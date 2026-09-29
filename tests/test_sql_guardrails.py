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

from planning.models import (
    QueryJoin,
    QueryPlan,
)

from semantic.models import (
    AggregationIntent,
    SemanticFilter,
    SemanticQuery,
)

from sql_compiler.compiler import (
    DeterministicSQLCompiler,
)

from sql_compiler.models import (
    CompiledQuery,
)

from sql_guardrails.guardrails import (
    SQLGuardrails,
)

from sql_validator.models import (
    SQLValidationResult,
)

from sql_validator.validator import (
    SQLValidator,
)


# ==========================================================
# GQ001 guardrail validation
#
# Verify a valid customer-payment aggregate query passes
# structural validation and execution guardrails.
# ==========================================================

def test_customer_payment_query_passes_guardrails():

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
        #
        # Temporary POC behavior should produce:
        #
        # name ILIKE '%Blue Prism%'
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
        # 7. QueryPlan
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
                    columns,
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
        # 9. SQL validation
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

        # ==================================================
        # 10. Guardrails
        # ==================================================

        guardrail_result = (
            SQLGuardrails()
            .evaluate(
                compiled=
                    compiled,

                plan=
                    query_plan,

                validation=
                    validation,
            )
        )

        assert (
            guardrail_result.allowed
            is True
        )

        assert (
            guardrail_result.violations
            == []
        )

    finally:

        neo4j.close()


# ==========================================================
# Guardrail negative test
#
# A join whose ontology validation status is not confirmed
# must be blocked.
# ==========================================================

def test_guardrails_reject_unconfirmed_join():

    plan = (
        QueryPlan(
            intent=
                "aggregate",

            grain=[
                "public.erp_customer_details"
            ],

            tables=[
                "public.erp_customer_details",
                "public.erp_payment_receipt",
            ],

            joins=[
                QueryJoin(
                    left_table=(
                        "public.erp_payment_receipt"
                    ),

                    left_column=
                        "customer_id",

                    right_table=(
                        "public.erp_customer_details"
                    ),

                    right_column=
                        "id",

                    join_type=
                        "INNER",

                    validation_status=
                        "inferred",
                )
            ],

            filters=[],

            aggregations=[],

            limit=None,
        )
    )

    compiled = (
        CompiledQuery(
            sql=(
                "SELECT COUNT(t1.id) AS count_id\n"
                "FROM "
                "public.erp_customer_details AS t0\n"
                "INNER JOIN "
                "public.erp_payment_receipt AS t1\n"
                "    ON "
                "t1.customer_id = t0.id"
            ),

            parameters=[],
        )
    )

    validation = (
        SQLValidationResult(
            valid=True,
            errors=[],
            warnings=[],
        )
    )

    result = (
        SQLGuardrails()
        .evaluate(
            compiled=
                compiled,

            plan=
                plan,

            validation=
                validation,
        )
    )

    assert (
        result.allowed
        is False
    )

    assert any(
        "non-confirmed"
        in violation

        for violation
        in result.violations
    )