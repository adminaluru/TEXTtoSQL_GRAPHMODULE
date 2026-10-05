from collections import defaultdict, deque
import time
from collections.abc import Callable
from typing import Protocol

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
)

from fastapi.responses import (
    JSONResponse,
)

from config import settings

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


def _resolve_api_key(
    api_key_override: str | None = None,
) -> str | None:
    """Return the configured API key if authentication is enabled."""
    if api_key_override is not None:
        return api_key_override

    configured = getattr(settings, "api_key", None)
    if configured is None:
        return None

    if hasattr(configured, "get_secret_value"):
        return configured.get_secret_value()

    return str(configured)


def _resolve_rate_limit(
    rate_limit_requests_per_minute: int | None = None,
) -> int | None:
    """Resolve the configured request rate limit."""
    if rate_limit_requests_per_minute is not None:
        limit = int(rate_limit_requests_per_minute)
        return limit if limit > 0 else None

    configured = getattr(settings, "rate_limit_requests_per_minute", 60)
    limit = int(configured)
    return limit if limit > 0 else None


def _client_key(request: Request) -> str:
    """Best-effort client identity for a rate-limit bucket."""
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded:
        return forwarded

    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip

    if request.client is not None:
        return request.client.host

    return "unknown"


def _require_api_key(
    api_key_override: str | None = None,
):
    """Require a bearer token when an API key is configured."""

    def dependency(request: Request) -> None:
        expected = _resolve_api_key(api_key_override)
        if expected is None:
            return None

        authorization = request.headers.get("Authorization", "").strip()
        x_api_key = request.headers.get("X-API-Key", "").strip()

        provided = ""

        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() == "bearer":
                provided = token.strip()

        if not provided and x_api_key:
            provided = x_api_key

        if provided != expected:
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key.",
            )

        return None

    return dependency


def create_app(
    research_agent: ResearchAgentProtocol,
    api_key: str | None = None,
    rate_limit_requests_per_minute: int | None = None,
    startup_validator: Callable[[], None] | None = None,
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

    limit = _resolve_rate_limit(rate_limit_requests_per_minute)

    app = FastAPI(
        title="FlowOne Research Agent",
        version="1.0.0",
    )

    app.state.rate_limit_windows = defaultdict(deque)

    @app.on_event("startup")
    def run_startup_validation() -> None:
        if startup_validator is None:
            return

        try:
            startup_validator()
        except Exception as exc:
            raise RuntimeError(
                "Application startup validation failed."
            ) from exc

    @app.middleware("http")
    async def enforce_rate_limit(request: Request, call_next):
        if request.url.path != "/ask":
            return await call_next(request)

        if limit is None:
            return await call_next(request)

        now = time.monotonic()
        key = _client_key(request)
        history = app.state.rate_limit_windows[key]

        while history and now - history[0] > 60:
            history.popleft()

        if len(history) >= limit:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please try again later.",
                },
            )

        history.append(now)
        return await call_next(request)

    # ======================================================
    # Health
    # ======================================================

    @app.get(
        "/health",
        summary="Legacy health check",
    )
    def health() -> dict[str, str]:
        return {
            "status": "ok",
        }

    @app.get(
        "/healthz",
        summary="Liveness probe",
    )
    def healthz() -> dict[str, str]:
        return {
            "status": "ok",
        }

    @app.get(
        "/readyz",
        summary="Readiness probe",
    )
    def readyz() -> dict[str, str]:
        return {
            "status": "ready",
        }

    # ======================================================
    # Ask
    # ======================================================

    @app.post(
        "/ask",
        response_model=AnswerResponse,
        responses={
            400: {
                "model": APIErrorResponse,
            },
            401: {
                "model": APIErrorResponse,
            },
            403: {
                "model": APIErrorResponse,
            },
            429: {
                "model": APIErrorResponse,
            },
            503: {
                "model": APIErrorResponse,
            },
            500: {
                "model": APIErrorResponse,
            },
        },
        dependencies=[Depends(_require_api_key(api_key))],
    )
    def ask(
        request: AskRequest,
    ) -> AnswerResponse:

        question = request.question.strip()

        if not question:
            raise HTTPException(
                status_code=400,
                detail="Question cannot be empty.",
            )

        try:
            return research_agent.ask(question)

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

        except (ConnectionError, TimeoutError, RuntimeError) as exc:
            detail = str(exc).strip() or "Dependency temporarily unavailable."
            raise HTTPException(
                status_code=503,
                detail=f"Dependency temporarily unavailable: {detail}",
            ) from exc

        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="Research Agent failed to process the request.",
            ) from exc

    return app
