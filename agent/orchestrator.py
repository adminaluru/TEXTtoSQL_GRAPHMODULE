import logging
import time

from dataclasses import dataclass

from agent.repair import (
    REPAIR_WARNING,
    UNSUPPORTED_MESSAGE,
    RepairState,
    UnsupportedQuestionError,
    build_repair_messages,
    clarification,
    fanout_message,
    unresolved_message,
)

from ai.interfaces.chat_model import (
    ChatMessage,
)

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

from observability.perf import (
    PerfTrace,
    request_id_var,
)

from nlu.question_analyzer import (
    QuestionAnalyzer,
)

from planning.column_resolver import (
    ColumnResolver,
)

from planning.fanout_guard import (
    FanOutGuard,
)

from planning.graph_models import (
    GraphPlan,
)

from planning.graph_planner import (
    GraphPlanner,
)

from planning.grain_resolver import (
    GrainResolver,
)

from planning.models import (
    QueryPlan,
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


@dataclass
class _Interpretation:
    """Output of interpretation stages 1-7."""

    planning_analysis: QuestionAnalysis

    graph_plan: GraphPlan

    query_plan: QueryPlan


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
    FanOutGuard
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

    Repair loop:

    When an interpretation stage (1-7) rejects the LLM's
    analysis with a ValueError, the rejection is fed back
    to the LLM and interpretation restarts from scratch,
    up to max_repair_attempts retries within
    repair_budget_seconds. When repair is exhausted, the
    question is unsupported, or an aggregation would
    double-count, a "clarification" AnswerResponse asks
    the user to rephrase instead of failing.

    Performance instrumentation measures each major
    stage independently through PerfTrace.

    IMPORTANT:

    Timing instrumentation does NOT change:
    - business logic
    - query semantics
    - SQL
    - API response
    - database execution
    """

    PERF_STAGES = [
        "question_analyzer",
        "semantic_normalizer",
        "semantic_adapter",
        "graph_planner",
        "grain_resolver",
        "column_resolver",
        "query_plan_builder",
        "fanout_guard",
        "sql_compiler",
        "sql_validator",
        "sql_guardrails",
        "postgresql",
        "result_analyzer",
        "answer_service",
    ]

    def __init__(
        self,
        question_analyzer: QuestionAnalyzer,
        semantic_query_normalizer:
            SemanticQueryNormalizer,
        graph_planner: GraphPlanner,
        grain_resolver: GrainResolver,
        column_resolver: ColumnResolver,
        query_plan_builder: QueryPlanBuilder,
        fanout_guard: FanOutGuard,
        sql_compiler: DeterministicSQLCompiler,
        sql_validator: SQLValidator,
        sql_guardrails: SQLGuardrails,
        postgres_executor: PostgreSQLExecutor,
        result_analyzer: ResultAnalyzer,
        answer_service: AnswerService,
        max_repair_attempts: int = 2,
        repair_budget_seconds: float = 30.0,
    ) -> None:

        if max_repair_attempts < 0:

            raise ValueError(
                "max_repair_attempts cannot be negative."
            )

        self._max_repair_attempts = (
            max_repair_attempts
        )

        self._repair_budget_seconds = (
            repair_budget_seconds
        )

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

        self._fanout_guard = (
            fanout_guard
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
        #
        # Stage timings accumulate across repair attempts.
        # ==================================================

        with PerfTrace(
            event="FLOWONE_PERF",
            id_field="request_id",
            stage_order=self.PERF_STAGES,
            logger=logger,
            trace_id=request_id_var.get(),
            fields={
                "attempts": 0,
                "outcome": None,
            },
        ) as trace:

            # ==================================================
            # 1-7. Interpretation with bounded repair
            # ==================================================

            interpretation = (
                self._interpret_with_repair(
                    question=
                        question,

                    trace=
                        trace,
                )
            )

            if isinstance(
                interpretation,
                AnswerResponse,
            ):

                return interpretation

            # ==================================================
            # 7B. FanOutGuard
            #
            # SUM / AVG / COUNT must not be inflated by a
            # one-to-many join from the measure table. Never
            # retried: dropping the offending concept would
            # answer a different question.
            # ==================================================

            with trace.stage("fanout_guard"):

                fanout_violations = (
                    self._fanout_guard
                    .find(
                        interpretation.query_plan
                    )
                )

            if fanout_violations:

                logger.info(
                    "FLOWONE_FANOUT_BLOCK request_id=%s "
                    "violations=%s",
                    trace.id,
                    [
                        violation.message
                        for violation
                        in fanout_violations
                    ],
                )

                trace.fields[
                    "outcome"
                ] = "clarification_fanout"

                return clarification(
                    fanout_message(
                        violations=
                            fanout_violations,

                        graph_plan=
                            interpretation.graph_plan,
                    )
                )

            # ==================================================
            # 8-13. Deterministic SQL, execution and answer
            # ==================================================

            answer = (
                self._execute(
                    interpretation=
                        interpretation,

                    trace=
                        trace,
                )
            )

            if trace.fields["attempts"] > 1:

                answer.warnings.append(
                    REPAIR_WARNING
                )

                trace.fields[
                    "outcome"
                ] = "answered_after_repair"

            else:

                trace.fields[
                    "outcome"
                ] = "answered"

            return answer

    # ======================================================
    # Interpretation + repair loop
    # ======================================================

    def _interpret_with_repair(
        self,
        question: str,
        trace: PerfTrace,
    ) -> "_Interpretation | AnswerResponse":
        """
        Run interpretation stages, retrying with LLM
        feedback when a stage rejects the interpretation.

        Bounded by max_repair_attempts retries and by
        repair_budget_seconds of total elapsed time (checked
        before each retry).

        Infrastructure errors (RuntimeError, ConnectionError,
        TimeoutError) and PermissionError propagate
        unchanged.
        """

        started = time.perf_counter()

        state = RepairState()

        repair_messages: list[ChatMessage] = []

        while True:

            trace.fields[
                "attempts"
            ] += 1

            state.raw_analysis = None
            state.graph_plan = None

            try:

                return self._interpret(
                    question=
                        question,

                    repair_messages=
                        repair_messages,

                    state=
                        state,

                    trace=
                        trace,
                )

            except UnsupportedQuestionError as exc:

                logger.info(
                    "FLOWONE_UNSUPPORTED request_id=%s "
                    "stage=%s reason=%s",
                    trace.id,
                    trace.current_stage,
                    exc,
                )

                trace.fields[
                    "outcome"
                ] = "clarification_unsupported"

                return clarification(
                    UNSUPPORTED_MESSAGE
                )

            except ValueError as exc:

                reason = (
                    f"{trace.current_stage}: {exc}"
                )

                state.failures.append(
                    reason
                )

                retries_used = (
                    trace.fields["attempts"] - 1
                )

                out_of_budget = (
                    time.perf_counter() - started
                    > self._repair_budget_seconds
                )

                can_retry = (
                    retries_used
                    < self._max_repair_attempts
                    and not out_of_budget
                )

                logger.info(
                    "FLOWONE_REPAIR request_id=%s attempt=%s "
                    "stage=%s error_type=%s retry=%s reason=%s",
                    trace.id,
                    trace.fields["attempts"],
                    trace.current_stage,
                    type(exc).__name__,
                    can_retry,
                    exc,
                )

                if not can_retry:

                    trace.fields[
                        "outcome"
                    ] = (
                        "clarification_budget"
                        if out_of_budget
                        else "clarification_unresolved"
                    )

                    return clarification(
                        unresolved_message(
                            state
                        )
                    )

                repair_messages = (
                    build_repair_messages(
                        state=
                            state,

                        reason=
                            reason,
                    )
                )

    def _interpret(
        self,
        question: str,
        repair_messages: list[ChatMessage],
        state: RepairState,
        trace: PerfTrace,
    ) -> "_Interpretation":
        """
        Stages 1-7: natural language -> physical QueryPlan.

        Every attempt re-runs the full deterministic
        validation; nothing from a rejected attempt is
        trusted.
        """

        # ==================================================
        # 1. QuestionAnalyzer
        #
        # Includes prompt preparation, the provider call,
        # structured response parsing and ontology
        # canonicalization.
        # ==================================================

        with trace.stage("question_analyzer"):

            raw_analysis = (
                self._question_analyzer
                .analyze(
                    question,
                    repair_messages=
                        repair_messages,
                )
            )

        state.raw_analysis = raw_analysis

        # ==================================================
        # 2. SemanticQueryNormalizer
        # ==================================================

        with trace.stage("semantic_normalizer"):

            normalization = (
                self._semantic_query_normalizer
                .normalize(
                    raw_analysis
                )
            )

        if (
            normalization.status
            != "ready"
        ):

            detail = (
                "Semantic normalization failed. "
                f"Status: "
                f"{normalization.status}. "
                f"Reason: "
                f"{normalization.reason_code}. "
                f"Message: "
                f"{normalization.message}"
            )

            # "invalid" means the LLM output is internally
            # inconsistent and may be repaired. Unsupported
            # and clarification outcomes are capability
            # limits.
            if normalization.status == "invalid":

                raise ValueError(
                    detail
                )

            raise UnsupportedQuestionError(
                detail
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
        # ==================================================

        with trace.stage("semantic_adapter"):

            planning_analysis = (
                self._to_planning_analysis(
                    semantic_query
                )
            )

        # ==================================================
        # 4. GraphPlanner
        # ==================================================

        with trace.stage("graph_planner"):

            graph_plan = (
                self._graph_planner
                .plan(
                    analysis=
                        planning_analysis,

                    semantic_query=
                        semantic_query,
                )
            )

        state.graph_plan = graph_plan

        # ==================================================
        # 5. GrainResolver
        # ==================================================

        with trace.stage("grain_resolver"):

            grain = (
                self._grain_resolver
                .resolve(
                    analysis=
                        planning_analysis,

                    graph_plan=
                        graph_plan,
                )
            )

        # ==================================================
        # 6. ColumnResolver
        #
        # Neo4j-backed column metadata resolution.
        # ==================================================

        with trace.stage("column_resolver"):

            columns = (
                self._column_resolver
                .resolve(
                    analysis=
                        planning_analysis,

                    graph_plan=
                        graph_plan,
                )
            )

        # ==================================================
        # 7. QueryPlanBuilder
        #
        # For COUNT(entity), this may also consult Neo4j
        # entity identity / PK metadata.
        # ==================================================

        with trace.stage("query_plan_builder"):

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

        return _Interpretation(
            planning_analysis=
                planning_analysis,

            graph_plan=
                graph_plan,

            query_plan=
                query_plan,
        )

    # ======================================================
    # Deterministic execution
    # ======================================================

    def _execute(
        self,
        interpretation: "_Interpretation",
        trace: PerfTrace,
    ) -> AnswerResponse:
        """
        Stages 8-13. Failures here are deterministic
        (compiler, validator, guardrails, database) and are
        never retried through the LLM.
        """

        query_plan = (
            interpretation.query_plan
        )

        # ==================================================
        # 8. SQLCompiler
        # ==================================================

        with trace.stage("sql_compiler"):

            compiled = (
                self._sql_compiler
                .compile(
                    query_plan
                )
            )

        # ==================================================
        # 9. SQLValidator
        # ==================================================

        with trace.stage("sql_validator"):

            validation = (
                self._sql_validator
                .validate(
                    compiled=
                        compiled,

                    plan=
                        query_plan,
                )
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

        with trace.stage("sql_guardrails"):

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

        with trace.stage("postgresql"):

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

        # ==================================================
        # 12. ResultAnalyzer
        # ==================================================

        with trace.stage("result_analyzer"):

            result = (
                self._result_analyzer
                .analyze(
                    execution=
                        execution,

                    plan=
                        query_plan,
                )
            )

        # ==================================================
        # 13. AnswerService
        # ==================================================

        with trace.stage("answer_service"):

            answer = (
                self._answer_service
                .build(
                    analysis=
                        interpretation.planning_analysis,

                    result=
                        result,
                )
            )

        return answer

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

            raise UnsupportedQuestionError(
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

                raise UnsupportedQuestionError(
                    "Current planning compatibility "
                    "adapter supports entity grouping "
                    "only."
                )

            if (
                grouping.attribute
                is not None
            ):

                raise UnsupportedQuestionError(
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