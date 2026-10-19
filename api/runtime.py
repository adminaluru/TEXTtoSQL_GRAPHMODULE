import logging

from psycopg_pool import (
    ConnectionPool,
)

from ai.providers.openai_chat import (
    OpenAIChatModel,
)

from agent.factory import (
    build_research_agent,
)

from api.application import (
    create_app,
)
from api.deployment_safety import check_deployment
from api.document_setup import document_service_from, start_document_processing
from agent.hybrid import HybridAssistant, load_route_vocabulary
from nlu.route_classifier import LLMRouteClassifier
from question_router import QuestionRouter
from execution.pool_reset import reset_session
from api.identity_setup import (
    identity_verifier_from,
    require_safe_auth_mode,
    tenant_resolver_from_file,
)
from clarification_state import PendingSelectionStore
from planning.entity_choice_labels import load_choice_labels
from planning.tenant_entity_scope import TenantEntityScopePolicy

from config import settings

from database.neo4j import (
    Neo4jClient,
)

from execution.postgres_executor import (
    PostgreSQLExecutor,
)

from ontology.cache import (
    TTLCache,
)


api_key = settings.api_key.get_secret_value() if getattr(settings, "api_key", None) is not None else None


# ==========================================================
# REAL LLM
#
# Model is controlled by OPENAI_MODEL.
#
# Example:
#   OPENAI_MODEL=gpt-5.6-luna
# ==========================================================

chat_model = OpenAIChatModel(
    timeout_seconds=settings.llm_timeout_seconds,
    max_retries=settings.llm_max_retries,
)


# ==========================================================
# NEO4J + ontology cache
# ==========================================================

neo4j = Neo4jClient()

ontology_cache = TTLCache(
    ttl_seconds=settings.ontology_cache_ttl_seconds,
)


# ==========================================================
# POSTGRESQL connection pool
#
# Opened at startup, closed at shutdown. check_connection
# discards connections broken by a database restart.
# ==========================================================

postgres_pool = ConnectionPool(
    settings.postgres_dsn,
    min_size=settings.postgres_pool_min_size,
    max_size=settings.postgres_pool_max_size,
    timeout=settings.postgres_pool_timeout_seconds,
    check=ConnectionPool.check_connection,
    reset=reset_session,
    open=False,
)

logger = logging.getLogger("uvicorn.error")

postgres_executor = PostgreSQLExecutor(
    statement_timeout_ms=5000,
    max_rows=100,
    pool=postgres_pool,
)


def _validate_runtime_dependencies() -> None:
    """Open the pool, then fail fast when backing services are unavailable."""
    postgres_pool.open()

    if not neo4j.health_check():
        raise RuntimeError("Neo4j dependency unavailable.")

    if not postgres_executor.health_check():
        raise RuntimeError("PostgreSQL dependency unavailable.")

    # Least privilege and TLS (Step 7): refuses to start in
    # production; logged as warnings in development.
    for finding in check_deployment(settings, postgres_executor.privileges()):
        logger.warning("FLOWONE_DEPLOYMENT_WARNING %s", finding)


# ==========================================================
# RESEARCH AGENT
# ==========================================================

research_agent = build_research_agent(
    chat_model=chat_model,

    neo4j_client=neo4j,

    postgres_executor=postgres_executor,

    ontology_cache=ontology_cache,

    max_repair_attempts=settings.agent_repair_max_attempts,

    repair_budget_seconds=settings.agent_repair_budget_seconds,

    request_timeout_seconds=settings.request_timeout_seconds,
)


# ==========================================================
# CALLER IDENTITY (Step 5)
#
# Production refuses to start without per-caller identity.
# In jwt mode each request gets an agent scoped to the
# caller's active tenant; heavy resources (Neo4j client,
# ontology cache, PostgreSQL pool, LLM client) are shared.
# ==========================================================

require_safe_auth_mode(settings)

identity = None
agent_for = None

if settings.auth_mode == "jwt":

    identity = identity_verifier_from(settings)

    tenant_resolver = tenant_resolver_from_file(
        settings.tenant_scope_file
    )

    choice_labels = load_choice_labels()

    clarification_store = PendingSelectionStore()

    def agent_for(access):
        return build_research_agent(
            chat_model=chat_model,
            neo4j_client=neo4j,
            postgres_executor=postgres_executor,
            ontology_cache=ontology_cache,
            max_repair_attempts=settings.agent_repair_max_attempts,
            repair_budget_seconds=settings.agent_repair_budget_seconds,
            request_timeout_seconds=settings.request_timeout_seconds,
            entity_scope_policy=TenantEntityScopePolicy(tenant_resolver, access),
            entity_choice_labels=choice_labels,
            clarification_store=clarification_store,
        )


# ==========================================================
# DOCUMENTS (Step 8, decision D-010)
#
# jwt mode only: each document belongs to the uploader's
# workspace. Processing runs in a background worker.
# ==========================================================

documents = None
document_worker = None
document_search = None

if settings.auth_mode == "jwt" and settings.documents_enabled:

    document_parts = document_service_from(settings)
    documents = document_parts.service
    document_worker = document_parts.worker
    document_search = document_parts.retriever


# ==========================================================
# ROUTING (Step 11, decision D-011)
#
# Each question goes to the database, the documents or both.
# Database-only questions use the research agent unchanged.
# ==========================================================

if agent_for is not None and document_search is not None and settings.routing_enabled:

    route_router = QuestionRouter(
        load_route_vocabulary(),
        LLMRouteClassifier(chat_model) if settings.route_classifier_enabled else None,
    )
    research_agent_for = agent_for

    def agent_for(access):
        return HybridAssistant(
            research_agent_for(access),
            route_router,
            access,
            document_retriever=document_search,
            has_documents=document_parts.has_documents,
            request_timeout_seconds=settings.request_timeout_seconds,
        )


# ==========================================================
# FASTAPI
# ==========================================================

app = create_app(
    research_agent,
    api_key=api_key,
    identity=identity,
    agent_for=agent_for,
    cors_allowed_origins=settings.cors_allowed_origins,
    startup_validator=_validate_runtime_dependencies,
    # The LLM provider is deliberately excluded: a third-party
    # outage should not pull every replica out of rotation.
    readiness_checks={
        "neo4j": neo4j.health_check,
        "postgresql": postgres_executor.health_check,
    },
    trusted_proxies=settings.trusted_proxies,
    clear_ontology_cache=ontology_cache.clear,
    documents=documents,
    document_max_file_bytes=settings.document_max_file_mb * 1024 * 1024,
    document_search=document_search,
)


@app.on_event("startup")
def start_documents() -> None:

    if documents is not None:
        start_document_processing(documents, document_worker)


# ==========================================================
# CLEAN SHUTDOWN
# ==========================================================

@app.on_event("shutdown")
def shutdown_resources() -> None:

    if document_worker is not None:
        document_worker.stop()

    postgres_pool.close()

    neo4j.close()
