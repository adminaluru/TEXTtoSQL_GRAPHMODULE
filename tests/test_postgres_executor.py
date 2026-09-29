from decimal import Decimal

import psycopg
import pytest

from database.neo4j import (
    Neo4jClient,
)

from execution.postgres_executor import (
    PostgreSQLExecutor,
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

from sql_compiler.models import (
    CompiledQuery,
)

from sql_guardrails.guardrails import (
    SQLGuardrails,
)

from sql_guardrails.models import (
    GuardrailResult,
)

from sql_validator.validator import (
    SQLValidator,
)

from sql_validator.models import (
    SQLValidationResult,
)


# ==========================================================
# GQ001 execution slice
#
# How much has the matching customer group paid?
#
# Current Golden Data:
#
# Customer name ILIKE '%Blue Prism%'
# Payment total = 24000.00
# ==========================================================

def test_customer_payment_postgres_execution():

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
        #
        # ColumnResolver temporarily turns the name filter
        # into:
        #
        #     ILIKE '%Blue Prism%'
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
        # 6. Columns
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
        # Verify temporary POC matching behavior
        # ==================================================

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
        #
        # NEW contract:
        #
        # QueryPlanBuilder(ontology)
        #
        # and:
        #
        # semantic_query=semantic_query
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
        # 8. SQL compilation
        # ==================================================

        compiled = (
            DeterministicSQLCompiler()
            .compile(
                query_plan
            )
        )

        # ==================================================
        # Parameterization safety
        #
        # The actual value must remain outside SQL text.
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
        # 10. SQL guardrails
        # ==================================================

        guardrails = (
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
            guardrails.allowed
            is True
        )

        # ==================================================
        # 11. PostgreSQL execution
        # ==================================================

        result = (
            PostgreSQLExecutor(
                statement_timeout_ms=
                    5000,

                max_rows=
                    100,
            )
            .execute(
                compiled=
                    compiled,

                validation=
                    validation,

                guardrails=
                    guardrails,
            )
        )

        # ==================================================
        # 12. Execution assertions
        # ==================================================

        assert (
            result.row_count
            == 1
        )

        assert (
            result.truncated
            is False
        )

        assert (
            "sum_payment_amount"
            in result.columns
        )

        assert (
            len(
                result.rows
            )
            == 1
        )

        assert (
            "sum_payment_amount"
            in result.rows[0]
        )

        # ==================================================
        # Current verified Golden Data
        #
        # Blue Prism partial-name match:
        #
        # 6 customer records
        # payment total = 24000.00
        # ==================================================

        assert (
            result.rows[0][
                "sum_payment_amount"
            ]
            == Decimal(
                "24000.00"
            )
        )

    finally:

        neo4j.close()


# ==========================================================
# Database-level read-only protection
# ==========================================================

def test_executor_database_read_only_protection():
    """
    Defense-in-depth test.

    We intentionally forge successful validator and
    guardrail results and attempt a DELETE.

    PostgreSQL READ ONLY transaction must still block it.
    """

    compiled = (
        CompiledQuery(
            sql=(
                "DELETE FROM "
                "public.erp_customer_details "
                "WHERE 1 = 0"
            ),

            parameters=[],
        )
    )

    fake_validation = (
        SQLValidationResult(
            valid=True,
            errors=[],
            warnings=[],
        )
    )

    fake_guardrails = (
        GuardrailResult(
            allowed=True,
            violations=[],
            warnings=[],
        )
    )

    executor = (
        PostgreSQLExecutor(
            statement_timeout_ms=
                5000,

            max_rows=
                10,
        )
    )

    with pytest.raises(
        psycopg.errors
        .ReadOnlySqlTransaction
    ):

        executor.execute(
            compiled=
                compiled,

            validation=
                fake_validation,

            guardrails=
                fake_guardrails,
        )