from ai.providers.openai_chat import (
    OpenAIChatModel,
)

from agent.factory import (
    build_research_agent,
)

from api.application import (
    create_app,
)

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


# ==========================================================
# RESEARCH AGENT
# ==========================================================

research_agent = build_research_agent(
    chat_model=chat_model,

    neo4j_client=neo4j,

    postgres_executor=PostgreSQLExecutor(
        statement_timeout_ms=5000,
        max_rows=100,
    ),
)


# ==========================================================
# FASTAPI
# ==========================================================

app = create_app(
    research_agent
)


# ==========================================================
# CLEAN SHUTDOWN
# ==========================================================

@app.on_event("shutdown")
def shutdown_resources() -> None:

    neo4j.close()