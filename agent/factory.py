from ai.interfaces.chat_model import (
    ChatModel,
)

from answering.answer_service import (
    AnswerService,
)

from agent.orchestrator import (
    ResearchAgentOrchestrator,
)

from database.neo4j import (
    Neo4jClient,
)

from execution.postgres_executor import (
    PostgreSQLExecutor,
)

from nlu.question_analyzer import (
    QuestionAnalyzer,
)

from ontology.concept_catalog import (
    ConceptCatalogService,
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

from result_analysis.analyzer import (
    ResultAnalyzer,
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


def build_research_agent(
    chat_model: ChatModel,
    neo4j_client: Neo4jClient,
    postgres_executor: PostgreSQLExecutor | None = None,
) -> ResearchAgentOrchestrator:
    """
    Build the complete Research Agent dependency graph.

    Architectural boundaries:

    LLM-dependent:
        QuestionAnalyzer

    Semantic trust boundary:
        SemanticQueryNormalizer

    Neo4j / deterministic planning:
        GraphPlanner
        GrainResolver
        ColumnResolver
        QueryPlanBuilder

    Deterministic SQL:
        DeterministicSQLCompiler
        SQLValidator
        SQLGuardrails

    Execution:
        PostgreSQLExecutor

    Result handling:
        ResultAnalyzer
        AnswerService

    The factory remains provider-neutral because callers
    supply any ChatModel implementation.

    Examples:

        OpenAIChatModel
        MockChatModel
        future BedrockChatModel
        future AnthropicChatModel

    No provider-specific SDK is referenced here.
    """

    # ======================================================
    # 1. Shared Neo4j-backed ontology infrastructure
    # ======================================================

    ontology = (
        OntologyService(
            neo4j_client
        )
    )

    concept_catalog = (
        ConceptCatalogService(
            neo4j_client
        )
    )

    # ======================================================
    # 2. LLM-dependent NLU
    #
    # Natural language
    #       ↓
    # QuestionAnalysis
    #
    # QuestionAnalyzer remains provider-independent because
    # it receives the ChatModel abstraction.
    # ======================================================

    question_analyzer = (
        QuestionAnalyzer(
            chat_model=
                chat_model,

            concept_catalog=
                concept_catalog,
        )
    )

    # ======================================================
    # 3. Semantic trust boundary
    #
    # QuestionAnalysis
    #       ↓
    # SemanticQueryNormalizer
    #       ↓
    # Canonical SemanticQuery
    #
    # This prevents raw LLM wording from directly driving
    # deterministic physical planning.
    # ======================================================

    semantic_normalizer = (
        SemanticQueryNormalizer(
            concept_catalog
        )
    )

    # ======================================================
    # 4. Deterministic graph planning
    # ======================================================

    graph_planner = (
        GraphPlanner(
            ontology
        )
    )

    # ======================================================
    # 5. Deterministic grain resolution
    # ======================================================

    grain_resolver = (
        GrainResolver()
    )

    # ======================================================
    # 6. Deterministic business attribute ->
    #    physical column resolution
    #
    # ColumnResolver remains responsible only for genuine
    # business attributes.
    #
    # Example:
    #
    # customer name
    #       ↓
    # erp_customer_details.name
    #
    # payment amount
    #       ↓
    # erp_payment_receipt.payment_amount
    #
    # Entity COUNT semantics should not be interpreted here.
    # ======================================================

    column_resolver = (
        ColumnResolver(
            ontology
        )
    )

    # ======================================================
    # 7. Physical QueryPlan construction
    #
    # QueryPlanBuilder now receives OntologyService because
    # entity aggregations such as:
    #
    # COUNT Invoice
    #
    # need to resolve the physical entity identity from
    # ontology metadata.
    #
    # This remains dynamic:
    #
    # Invoice
    #    ↓
    # resolved table from GraphPlan
    #    ↓
    # PK/entity identity from Neo4j metadata
    #
    # No Invoice -> id hard-coded mapping is introduced.
    # ======================================================

    query_plan_builder = (
        QueryPlanBuilder(
            ontology
        )
    )

    # ======================================================
    # 8. Deterministic SQL compilation
    # ======================================================

    sql_compiler = (
        DeterministicSQLCompiler()
    )

    # ======================================================
    # 9. Structural SQL validation
    # ======================================================

    sql_validator = (
        SQLValidator()
    )

    # ======================================================
    # 10. SQL execution guardrails
    # ======================================================

    sql_guardrails = (
        SQLGuardrails()
    )

    # ======================================================
    # 11. PostgreSQL execution
    #
    # Tests may inject their own executor.
    # Runtime uses the default executor.
    # ======================================================

    executor = (
        postgres_executor
        or PostgreSQLExecutor()
    )

    # ======================================================
    # 12. Result analysis
    # ======================================================

    result_analyzer = (
        ResultAnalyzer()
    )

    # ======================================================
    # 13. Final factual answer construction
    # ======================================================

    answer_service = (
        AnswerService()
    )

    # ======================================================
    # 14. Assemble Research Agent
    # ======================================================

    return ResearchAgentOrchestrator(
        question_analyzer=
            question_analyzer,

        semantic_query_normalizer=
            semantic_normalizer,

        graph_planner=
            graph_planner,

        grain_resolver=
            grain_resolver,

        column_resolver=
            column_resolver,

        query_plan_builder=
            query_plan_builder,

        sql_compiler=
            sql_compiler,

        sql_validator=
            sql_validator,

        sql_guardrails=
            sql_guardrails,

        postgres_executor=
            executor,

        result_analyzer=
            result_analyzer,

        answer_service=
            answer_service,
    )