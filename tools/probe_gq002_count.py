from ai.providers.mock_chat import (
    MockChatModel,
)

from agent.factory import (
    build_research_agent,
)

from database.neo4j import (
    Neo4jClient,
)

from execution.postgres_executor import (
    PostgreSQLExecutor,
)


def main() -> None:

    print("=" * 70)
    print("GQ002 COUNT CAPABILITY PROBE")
    print("=" * 70)

    # ======================================================
    # Mock only the LLM interpretation.
    #
    # Everything after QuestionAnalyzer is the real
    # Research Agent runtime.
    # ======================================================

    chat_model = MockChatModel(
        structured_response={
            "intent": "count",

            "concepts": [
                "Customer",
                "Invoice",
            ],

            "result_concept":
                "Customer",

            "measure_concept":
                "Invoice",

            # COUNT requires a physical count target.
            # ColumnResolver should dynamically resolve
            # this through Neo4j column metadata.
            "measure_attribute":
                "id",

            "group_by_concepts": [],

            "entities": [
                {
                    "concept":
                        "Customer",

                    "attribute":
                        "name",

                    "value":
                        "Blue Prism",
                }
            ],

            "aggregation":
                "COUNT",
        }
    )

    neo4j = Neo4jClient()

    try:

        agent = build_research_agent(
            chat_model=chat_model,

            neo4j_client=neo4j,

            postgres_executor=
                PostgreSQLExecutor(
                    statement_timeout_ms=5000,
                    max_rows=100,
                ),
        )

        response = agent.ask(
            "How many invoices does Blue Prism have?"
        )

        print()
        print("RESULT")
        print("-" * 70)

        print(
            response.model_dump()
        )

        print()
        print("ANSWER")
        print("-" * 70)

        print(
            response.answer
        )

        print()
        print("=" * 70)
        print("GQ002 PIPELINE COMPLETED")
        print("=" * 70)

    finally:

        neo4j.close()


if __name__ == "__main__":
    main()