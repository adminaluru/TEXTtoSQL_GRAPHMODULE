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

api_key = settings.api_key.get_secret_value() if getattr(settings, "api_key", None) is not None else None
from database.neo4j import (
    Neo4jClient,
)

from execution.postgres_executor import (
    PostgreSQLExecutor,
)


# ==========================================================
# REAL LLM
#
# Model is controlled by OPENAI_MODEL.
#
# Example:
#   OPENAI_MODEL=gpt-5.6-luna
# ==========================================================

chat_model = OpenAIChatModel()


# ==========================================================
# NEO4J
# ==========================================================

neo4j = Neo4jClient()

postgres_executor = PostgreSQLExecutor(
    statement_timeout_ms=5000,
    max_rows=100,
)


def _validate_runtime_dependencies() -> None:
    """Fail fast when required backing services are unavailable."""
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
)


# ==========================================================
# FASTAPI
# ==========================================================

app = create_app(
    research_agent,
    api_key=api_key,
    startup_validator=_validate_runtime_dependencies,
)


# ==========================================================
# CLEAN SHUTDOWN
# ==========================================================

@app.on_event("shutdown")
def shutdown_resources() -> None:

    neo4j.close()
