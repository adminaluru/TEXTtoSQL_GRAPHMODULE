import logging
import time
import uuid

from answering.answer_service import (
    AnswerService,
)

from answering.models import (
    AnswerResponse,
)

from execution.postgres_executor import (
    PostgreSQLExecutor,
)

from nlu.models import (
    QuestionAnalysis,
)

from nlu.question_analyzer import (
    QuestionAnalyzer,
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

from result_analysis.analyzer import (
    ResultAnalyzer,
)

from semantic.models import (
    SemanticQuery,
)

from semantic.normalizer import (
    SemanticQueryNormalizer,
)

from sql_compiler.compiler import (
    DeterministicSQLCompiler,
)

from sql_guardrails.guardrails import (
    SQLGuardrails,
)

from sql_validator.validator import (
    SQLValidator,
)


# ==========================================================
# Performance logger
#
# Using Uvicorn's logger means these messages will appear
# naturally when running:
#
# uvicorn api.runtime:app ...
#
# No user question or business data is logged here.
# ==========================================================

logger = logging.getLogger(
    "uvicorn.error"
)


class ResearchAgentOrchestrator:
    """
    End-to-end coordinator for the Research Agent.

    Pipeline:

    Natural language
        ↓
    QuestionAnalyzer
        ↓
    raw QuestionAnalysis
        ↓
    SemanticQueryNormalizer
        ↓
    canonical SemanticQuery
        ↓
    planning-compatible QuestionAnalysis
        ↓
    GraphPlanner
        ↓
    GrainResolver
        ↓
    ColumnResolver
        ↓
    QueryPlanBuilder
        ↓
    deterministic SQL
        ↓
    validation
        ↓
    guardrails
        ↓
    PostgreSQL
        ↓
    ResultAnalyzer
        ↓
    AnswerService

    Performance instrumentation measures each major
    stage independently.

    IMPORTANT:

    Timing instrumentation does NOT change:
    - business logic
    - query semantics
    - SQL
    - API response
    - database execution
    """

    def __init__(
        self,
        question_analyzer: QuestionAnalyzer,
        semantic_query_normalizer:
            SemanticQueryNormalizer,
        graph_planner: GraphPlanner,
        grain_resolver: GrainResolver,
        column_resolver: ColumnResolver,
        query_plan_builder: QueryPlanBuilder,
        sql_compiler: DeterministicSQLCompiler,
        sql_validator: SQLValidator,
        sql_guardrails: SQLGuardrails,
        postgres_executor: PostgreSQLExecutor,
        result_analyzer: ResultAnalyzer,
        answer_service: AnswerService,
    ) -> None:

        self._question_analyzer = (
            question_analyzer
        )

        self._semantic_query_normalizer = (
            semantic_query_normalizer
        )

        self._graph_planner = (
            graph_planner
        )

        self._grain_resolver = (
            grain_resolver
        )

        self._column_resolver = (
            column_resolver
        )

        self._query_plan_builder = (
            query_plan_builder
        )

        self._sql_compiler = (
            sql_compiler
        )

        self._sql_validator = (
            sql_validator
        )

        self._sql_guardrails = (
            sql_guardrails
        )

        self._postgres_executor = (
            postgres_executor
        )

        self._result_analyzer = (
            result_analyzer
        )

        self._answer_service = (
            answer_service
        )

    # ======================================================
    # Public API
    # ======================================================

    def ask(
        self,
        question: str,
    ) -> AnswerResponse:

        question = (
            question.strip()
        )

        if not question:

            raise ValueError(
                "Question cannot be empty."
            )

        # ==================================================
        # Request-level performance context
        # ==================================================

        request_id = (
            uuid.uuid4().hex[:12]
        )

        total_started = (
            time.perf_counter()
        )

        timings: dict[
            str,
            float,
        ] = {}

        status = "failed"

        current_stage = "startup"

        try:

            # ==================================================
            # 1. QuestionAnalyzer
            #
            # Includes:
            # - prompt preparation
            # - OpenAI/provider call
            # - structured response parsing
            # - QuestionAnalyzer-level ontology work
            #
            # We will instrument pure provider latency later
            # if this stage is dominant.
            # ==================================================

            current_stage = (
                "question_analyzer"
            )

            started = (
                time.perf_counter()
            )

            raw_analysis = (
                self._question_analyzer
                .analyze(
                    question
                )
            )

            timings[
                "question_analyzer"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 2. SemanticQueryNormalizer
            # ==================================================

            current_stage = (
                "semantic_normalizer"
            )

            started = (
                time.perf_counter()
            )

            normalization = (
                self._semantic_query_normalizer
                .normalize(
                    raw_analysis
                )
            )

            timings[
                "semantic_normalizer"
            ] = self._elapsed_ms(
                started
            )

            if (
                normalization.status
                != "ready"
            ):

                raise ValueError(
                    "Semantic normalization failed. "
                    f"Status: "
                    f"{normalization.status}. "
                    f"Reason: "
                    f"{normalization.reason_code}. "
                    f"Message: "
                    f"{normalization.message}"
                )

            semantic_query = (
                normalization.query
            )

            if semantic_query is None:

                raise ValueError(
                    "Semantic normalization returned "
                    "ready status without a query."
                )

            # ==================================================
            # 3. Compatibility adapter
            #
            # Normally extremely cheap, but we measure it
            # separately so hidden Python transformation
            # overhead is visible.
            # ==================================================

            current_stage = (
                "semantic_adapter"
            )

            started = (
                time.perf_counter()
            )

            planning_analysis = (
                self._to_planning_analysis(
                    semantic_query
                )
            )

            timings[
                "semantic_adapter"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 4. GraphPlanner
            # ==================================================

            current_stage = (
                "graph_planner"
            )

            started = (
                time.perf_counter()
            )

            graph_plan = (
                self._graph_planner
                .plan(
                    analysis=
                        planning_analysis,

                    semantic_query=
                        semantic_query,
                )
            )

            timings[
                "graph_planner"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 5. GrainResolver
            # ==================================================

            current_stage = (
                "grain_resolver"
            )

            started = (
                time.perf_counter()
            )

            grain = (
                self._grain_resolver
                .resolve(
                    analysis=
                        planning_analysis,

                    graph_plan=
                        graph_plan,
                )
            )

            timings[
                "grain_resolver"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 6. ColumnResolver
            #
            # Neo4j-backed column metadata resolution.
            # ==================================================

            current_stage = (
                "column_resolver"
            )

            started = (
                time.perf_counter()
            )

            columns = (
                self._column_resolver
                .resolve(
                    analysis=
                        planning_analysis,

                    graph_plan=
                        graph_plan,
                )
            )

            timings[
                "column_resolver"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 7. QueryPlanBuilder
            #
            # For COUNT(entity), this may also consult Neo4j
            # entity identity / PK metadata.
            # ==================================================

            current_stage = (
                "query_plan_builder"
            )

            started = (
                time.perf_counter()
            )

            query_plan = (
                self._query_plan_builder
                .build(
                    semantic_query=
                        semantic_query,

                    analysis=
                        planning_analysis,

                    graph_plan=
                        graph_plan,

                    grain=
                        grain,

                    columns=
                        columns,
                )
            )

            timings[
                "query_plan_builder"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 8. SQLCompiler
            # ==================================================

            current_stage = (
                "sql_compiler"
            )

            started = (
                time.perf_counter()
            )

            compiled = (
                self._sql_compiler
                .compile(
                    query_plan
                )
            )

            timings[
                "sql_compiler"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 9. SQLValidator
            # ==================================================

            current_stage = (
                "sql_validator"
            )

            started = (
                time.perf_counter()
            )

            validation = (
                self._sql_validator
                .validate(
                    compiled=
                        compiled,

                    plan=
                        query_plan,
                )
            )

            timings[
                "sql_validator"
            ] = self._elapsed_ms(
                started
            )

            if not validation.valid:

                raise ValueError(
                    "Generated SQL failed validation. "
                    f"Errors: "
                    f"{validation.errors}"
                )

            # ==================================================
            # 10. SQLGuardrails
            # ==================================================

            current_stage = (
                "sql_guardrails"
            )

            started = (
                time.perf_counter()
            )

            guardrails = (
                self._sql_guardrails
                .evaluate(
                    compiled=
                        compiled,

                    plan=
                        query_plan,

                    validation=
                        validation,
                )
            )

            timings[
                "sql_guardrails"
            ] = self._elapsed_ms(
                started
            )

            if not guardrails.allowed:

                raise PermissionError(
                    "Query execution blocked by "
                    "SQL guardrails. "
                    f"Violations: "
                    f"{guardrails.violations}"
                )

            # ==================================================
            # 11. PostgreSQL execution
            # ==================================================

            current_stage = (
                "postgresql"
            )

            started = (
                time.perf_counter()
            )

            execution = (
                self._postgres_executor
                .execute(
                    compiled=
                        compiled,

                    validation=
                        validation,

                    guardrails=
                        guardrails,
                )
            )

            timings[
                "postgresql"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 12. ResultAnalyzer
            # ==================================================

            current_stage = (
                "result_analyzer"
            )

            started = (
                time.perf_counter()
            )

            result = (
                self._result_analyzer
                .analyze(
                    execution=
                        execution,

                    plan=
                        query_plan,
                )
            )

            timings[
                "result_analyzer"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 13. AnswerService
            # ==================================================

            current_stage = (
                "answer_service"
            )

            started = (
                time.perf_counter()
            )

            answer = (
                self._answer_service
                .build(
                    analysis=
                        planning_analysis,

                    result=
                        result,
                )
            )

            timings[
                "answer_service"
            ] = self._elapsed_ms(
                started
            )

            status = "success"

            return answer

        finally:

            # ==================================================
            # Request total
            #
            # This executes on both:
            # - success
            # - exception
            #
            # so slow failures can also be diagnosed.
            # ==================================================

            timings[
                "total"
            ] = self._elapsed_ms(
                total_started
            )

            self._log_timings(
                request_id=
                    request_id,

                status=
                    status,

                failed_stage=(
                    None
                    if status == "success"
                    else current_stage
                ),

                timings=
                    timings,
            )

    # ======================================================
    # Timing helper
    # ======================================================

    @staticmethod
    def _elapsed_ms(
        started: float,
    ) -> float:

        return round(
            (
                time.perf_counter()
                - started
            )
            * 1000,
            2,
        )

    # ======================================================
    # Structured performance logging
    # ======================================================

    @staticmethod
    def _log_timings(
        request_id: str,
        status: str,
        failed_stage: str | None,
        timings: dict[str, float],
    ) -> None:

        ordered_stages = [
            "question_analyzer",
            "semantic_normalizer",
            "semantic_adapter",
            "graph_planner",
            "grain_resolver",
            "column_resolver",
            "query_plan_builder",
            "sql_compiler",
            "sql_validator",
            "sql_guardrails",
            "postgresql",
            "result_analyzer",
            "answer_service",
            "total",
        ]

        timing_text = " ".join(
            (
                f"{stage}_ms="
                f"{timings[stage]:.2f}"
            )

            for stage
            in ordered_stages

            if stage in timings
        )

        logger.info(
            "FLOWONE_PERF "
            "request_id=%s "
            "status=%s "
            "failed_stage=%s "
            "%s",
            request_id,
            status,
            failed_stage or "-",
            timing_text,
        )

    # ======================================================
    # SemanticQuery -> existing planning contract
    # ======================================================

    @staticmethod
    def _to_planning_analysis(
        semantic_query: SemanticQuery,
    ) -> QuestionAnalysis:
        """
        Temporary compatibility adapter.

        SemanticQuery is authoritative.

        GraphPlanner, GrainResolver and ColumnResolver
        currently consume QuestionAnalysis.
        """

        if (
            len(
                semantic_query.aggregations
            )
            > 1
        ):

            raise ValueError(
                "Current planning pipeline supports "
                "at most one semantic aggregation."
            )

        aggregation = (
            semantic_query
            .aggregations[0]

            if semantic_query.aggregations

            else None
        )

        # ==================================================
        # Canonical intent
        # ==================================================

        if (
            aggregation is not None
            and aggregation.function
            == "COUNT"
        ):

            intent = "count"

        else:

            intent = (
                semantic_query.query_type
            )

        # ==================================================
        # Canonical filters
        # ==================================================

        entities = [
            {
                "concept":
                    semantic_filter.concept,

                "attribute":
                    semantic_filter.attribute,

                "value":
                    semantic_filter.value,
            }

            for semantic_filter
            in semantic_query.filters
        ]

        # ==================================================
        # Canonical grouping adapter
        #
        # SemanticQuery is authoritative.
        #
        # The current planning compatibility contract can
        # carry entity grouping only as group_by_concepts.
        #
        # Preserve those concepts rather than dropping them.
        # Attribute grouping must fail closed until the
        # planning contract can represent the attribute.
        # ==================================================

        group_by_concepts = []

        for grouping in (
            semantic_query.groupings
        ):

            if (
                grouping.target_kind
                != "entity"
            ):

                raise ValueError(
                    "Current planning compatibility "
                    "adapter supports entity grouping "
                    "only."
                )

            if (
                grouping.attribute
                is not None
            ):

                raise ValueError(
                    "Entity grouping cannot contain "
                    "an attribute in the current "
                    "planning compatibility contract."
                )

            if (
                grouping.concept
                not in semantic_query.concepts
            ):

                raise ValueError(
                    "Grouping concept must exist in "
                    "SemanticQuery concepts: "
                    f"{grouping.concept}"
                )

            if (
                grouping.concept
                not in group_by_concepts
            ):

                group_by_concepts.append(
                    grouping.concept
                )

        # ==================================================
        # Canonical planning representation
        # ==================================================

        return QuestionAnalysis(
            intent=
                intent,

            concepts=
                list(
                    semantic_query.concepts
                ),

            result_concept=
                semantic_query.result_concept,

            measure_concept=(
                aggregation.concept

                if aggregation
                is not None

                else None
            ),

            measure_attribute=(
                aggregation.attribute

                if aggregation
                is not None

                else None
            ),

            group_by_concepts=
                group_by_concepts,

            entities=
                entities,

            aggregation=(
                aggregation.function

                if aggregation
                is not None

                else None
            ),
        )