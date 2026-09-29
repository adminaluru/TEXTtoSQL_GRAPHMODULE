from typing import Protocol

from fastapi import (
    FastAPI,
    HTTPException,
)

from answering.models import (
    AnswerResponse,
)

from api.models import (
    APIErrorResponse,
    AskRequest,
)


class ResearchAgentProtocol(Protocol):
    """
    Minimal contract FastAPI needs from the
    Research Agent.

    FastAPI does not need to know the concrete
    orchestrator implementation.
    """

    def ask(
        self,
        question: str,
    ) -> AnswerResponse:
        ...


def create_app(
    research_agent: ResearchAgentProtocol,
) -> FastAPI:
    """
    Create the FastAPI application with an injected
    Research Agent.

    Dependency injection keeps the HTTP layer independent
    from:

    - LLM provider
    - Neo4j
    - PostgreSQL
    - SQL compiler
    - planner internals
    """

    app = FastAPI(
        title="FlowOne Research Agent",
        version="1.0.0",
    )

    # ======================================================
    # Health
    # ======================================================

    @app.get(
        "/health"
    )
    def health() -> dict[str, str]:

        return {
            "status": "ok"
        }

    # ======================================================
    # Ask
    # ======================================================

    @app.post(
        "/ask",
        response_model=AnswerResponse,
        responses={
            400: {
                "model":
                    APIErrorResponse,
            },
            403: {
                "model":
                    APIErrorResponse,
            },
            500: {
                "model":
                    APIErrorResponse,
            },
        },
    )
    def ask(
        request: AskRequest,
    ) -> AnswerResponse:

        question = (
            request.question.strip()
        )

        if not question:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Question cannot be empty."
                ),
            )

        try:

            return research_agent.ask(
                question
            )

        except PermissionError as exc:

            raise HTTPException(
                status_code=403,
                detail=str(exc),
            ) from exc

        except ValueError as exc:

            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

        except Exception as exc:

            # Do not expose database credentials,
            # SQL internals, stack traces, etc.
            raise HTTPException(
                status_code=500,
                detail=(
                    "Research Agent failed to "
                    "process the request."
                ),
            ) from exc

    return app