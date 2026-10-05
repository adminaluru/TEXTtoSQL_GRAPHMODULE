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
    open=False,
)

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
)


# ==========================================================
# FASTAPI
# ==========================================================

app = create_app(
    research_agent,
    api_key=api_key,
    startup_validator=_validate_runtime_dependencies,
    # The LLM provider is deliberately excluded: a third-party
    # outage should not pull every replica out of rotation.
    readiness_checks={
        "neo4j": neo4j.health_check,
        "postgresql": postgres_executor.health_check,
    },
    trusted_proxies=settings.trusted_proxies,
    clear_ontology_cache=ontology_cache.clear,
)


# ==========================================================
# CLEAN SHUTDOWN
# ==========================================================

@app.on_event("shutdown")
def shutdown_resources() -> None:

    postgres_pool.close()

    neo4j.close()
