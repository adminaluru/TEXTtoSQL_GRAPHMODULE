from collections import defaultdict, deque
import hmac
import ipaddress
import logging
import time
from collections.abc import Callable, Iterable
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

from fastapi.middleware.cors import CORSMiddleware

from api.document_routes import add_document_routes
from api.identity import authenticate_request, identify_request
from api.models import (
    APIErrorResponse,
    AskRequest,
    ClarifyRequest,
    MeResponse,
)
from agent.orchestrator import CallerContext
from clarification_state import ClarificationStateError
from document_ingestion import DocumentIngestionService
from document_retrieval import DocumentRetriever
from hybrid_contracts import AccessContext, ClarificationResponse
from observability.deadline import DeadlineExceeded
from trusted_identity import IdentityVerifier

from observability.perf import (
    new_request_id,
    request_id_var,
)


logger = logging.getLogger(
    "uvicorn.error"
)


# ==========================================================
# Client-facing error messages
#
# Internal details (tables, columns, guardrail violations,
# planner errors) are logged with the request id and never
# returned to the client.
# ==========================================================

BAD_REQUEST_MESSAGE = (
    "The question could not be answered. Try rephrasing it "
    "or naming the business entities explicitly."
)

FORBIDDEN_MESSAGE = (
    "The generated query was blocked by execution policy."
)

UNAVAILABLE_MESSAGE = (
    "Dependency temporarily unavailable. Please try again later."
)

INTERNAL_ERROR_MESSAGE = (
    "Research Agent failed to process the request."
)

# Rate-limit buckets idle longer than this are discarded.
RATE_LIMIT_WINDOW_SECONDS = 60


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


def _parse_networks(
    values: Iterable[str],
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse trusted proxy IPs / CIDR ranges."""
    return [
        ipaddress.ip_network(value.strip(), strict=False)
        for value in values
        if value.strip()
    ]


def _is_trusted(
    address: str,
    trusted_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False

    return any(ip in network for network in trusted_networks)


def _client_key(
    request: Request,
    trusted_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> str:
    """
    Client identity for a rate-limit bucket.

    Forwarding headers are client-controlled, so they are
    honoured only when the direct peer is a trusted proxy.
    X-Forwarded-For is walked right-to-left, skipping
    trusted proxies; the first untrusted hop is the client.
    """
    peer = request.client.host if request.client is not None else "unknown"

    if not trusted_networks or not _is_trusted(peer, trusted_networks):
        return peer

    forwarded = [
        hop.strip()
        for hop in request.headers.get("X-Forwarded-For", "").split(",")
        if hop.strip()
    ]

    for hop in reversed(forwarded):
        if not _is_trusted(hop, trusted_networks):
            return hop

    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip

    return peer


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

        # Constant-time comparison avoids leaking the key
        # through response timing.
        if not hmac.compare_digest(
            provided.encode("utf-8"),
            expected.encode("utf-8"),
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key.",
            )

        return None

    return dependency


def _error_response(
    status_code: int,
    message: str,
    request_id: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": message,
            "request_id": request_id,
        },
        headers={
            "X-Request-ID": request_id,
        },
    )


def _answer_safely(
    request_id: str,
    call: Callable[[], AnswerResponse],
):
    """
    Run one agent call and turn failures into generic,
    client-safe responses. Details are logged with the
    request id only.
    """

    token = request_id_var.set(request_id)

    try:
        return call()

    except ClarificationStateError as exc:
        # Expired, replayed or someone else's choice. The
        # message is written for users.
        status_code = 409
        message = str(exc)
        error = exc

    except DeadlineExceeded as exc:
        # The whole question ran out of time (Step 7).
        status_code = 504
        message = str(exc)
        error = exc

    except PermissionError as exc:
        status_code = 403
        message = FORBIDDEN_MESSAGE
        error = exc

    except ValueError as exc:
        status_code = 400
        message = BAD_REQUEST_MESSAGE
        error = exc

    except (ConnectionError, TimeoutError, RuntimeError) as exc:
        status_code = 503
        message = UNAVAILABLE_MESSAGE
        error = exc

    except Exception as exc:
        status_code = 500
        message = INTERNAL_ERROR_MESSAGE
        error = exc

    finally:
        request_id_var.reset(token)

    logger.warning(
        "FLOWONE_API_ERROR request_id=%s status_code=%s "
        "error_type=%s error=%s",
        request_id,
        status_code,
        type(error).__name__,
        error,
        exc_info=status_code == 500,
    )

    return _error_response(
        status_code,
        message,
        request_id,
    )


def create_app(
    research_agent: ResearchAgentProtocol,
    api_key: str | None = None,
    rate_limit_requests_per_minute: int | None = None,
    startup_validator: Callable[[], None] | None = None,
    readiness_checks: dict[str, Callable[[], bool]] | None = None,
    trusted_proxies: Iterable[str] | None = None,
    clear_ontology_cache: Callable[[], None] | None = None,
    identity: IdentityVerifier | None = None,
    agent_for: Callable[[AccessContext], ResearchAgentProtocol] | None = None,
    cors_allowed_origins: Iterable[str] | None = None,
    documents: DocumentIngestionService | None = None,
    document_max_file_bytes: int = 25 * 1024 * 1024,
    document_search: DocumentRetriever | None = None,
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

    readiness_checks maps dependency names to health
    callables used by /readyz.

    trusted_proxies lists proxy IPs / CIDR ranges whose
    forwarding headers may be trusted for rate limiting.

    clear_ontology_cache, when supplied, is exposed as
    POST /admin/ontology-cache/clear (API-key protected
    when an API key is configured).

    identity, when supplied, switches /ask to verified
    bearer tokens (jwt mode) and adds POST /clarify.
    agent_for(access) then builds the agent for each caller;
    without it the shared research_agent is used.

    documents, in jwt mode, adds the /documents routes
    (upload, list, detail, retry, delete) for the caller's
    workspace; see api/document_routes.py.
    """

    limit = _resolve_rate_limit(rate_limit_requests_per_minute)

    trusted_networks = _parse_networks(
        trusted_proxies
        if trusted_proxies is not None
        else getattr(settings, "trusted_proxies", [])
    )

    app = FastAPI(
        title="FlowOne Research Agent",
        version="1.0.0",
    )

    # Cross-origin browser access is closed unless exact origins are
    # listed. Bearer tokens only: no cookies, so no credentials mode.
    origins = [o.strip() for o in (cors_allowed_origins or []) if o.strip()]
    if "*" in origins:
        raise ValueError("CORS origins must be listed explicitly; '*' is not allowed.")
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-FlowOne-Tenant"],
            expose_headers=["X-Request-ID"],
            allow_credentials=False,
        )

    app.state.rate_limit_windows = defaultdict(deque)
    app.state.rate_limit_last_sweep = time.monotonic()

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
        # Questions and uploads are limited; reads are not.
        limited = request.url.path == "/ask" or (
            request.method == "POST" and request.url.path in ("/documents", "/documents/search"))
        if not limited:
            return await call_next(request)

        if limit is None:
            return await call_next(request)

        now = time.monotonic()
        windows = app.state.rate_limit_windows

        # Periodically drop idle buckets so memory stays
        # bounded by recently active clients.
        if now - app.state.rate_limit_last_sweep > RATE_LIMIT_WINDOW_SECONDS:
            for idle_key in [
                key
                for key, history in windows.items()
                if not history or now - history[-1] > RATE_LIMIT_WINDOW_SECONDS
            ]:
                del windows[idle_key]

            app.state.rate_limit_last_sweep = now

        key = _client_key(request, trusted_networks)
        history = windows[key]

        while history and now - history[0] > RATE_LIMIT_WINDOW_SECONDS:
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
    def readyz():
        checks: dict[str, bool] = {}

        for name, check in (readiness_checks or {}).items():
            try:
                checks[name] = bool(check())
            except Exception:
                logger.warning(
                    "FLOWONE_READINESS check=%s failed",
                    name,
                    exc_info=True,
                )
                checks[name] = False

        if all(checks.values()):
            return {
                "status": "ready",
                "checks": checks,
            }

        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "checks": checks,
            },
        )

    # ======================================================
    # Admin
    # ======================================================

    if clear_ontology_cache is not None:

        @app.post(
            "/admin/ontology-cache/clear",
            summary="Drop cached ontology lookups",
            dependencies=[Depends(_require_api_key(api_key))],
        )
        def clear_cache() -> dict[str, str]:
            clear_ontology_cache()

            logger.info("FLOWONE_ONTOLOGY_CACHE cleared")

            return {
                "status": "cleared",
            }

    # ======================================================
    # Ask
    #
    # api_key mode: the shared key protects the route; there
    #   is no per-user identity (development only).
    # jwt mode: every request is authenticated into a verified
    #   AccessContext, and the agent is built for that caller
    #   (tenant scope, clarification binding).
    # ======================================================

    error_responses = {
        status: {"model": APIErrorResponse}
        for status in (400, 401, 403, 409, 429, 503, 504, 500)
    }

    route_dependencies = (
        []
        if identity is not None
        else [Depends(_require_api_key(api_key))]
    )

    def agent_for_caller(access):
        return (
            agent_for(access)
            if agent_for is not None
            else research_agent
        )

    @app.post(
        "/ask",
        response_model=AnswerResponse,
        responses=error_responses,
        dependencies=route_dependencies,
    )
    def ask(
        request: AskRequest,
        http_request: Request,
    ):

        request_id = new_request_id()

        question = request.question.strip()

        if not question:
            return _error_response(
                400,
                "Question cannot be empty.",
                request_id,
            )

        if identity is None:
            return _answer_safely(
                request_id,
                lambda: research_agent.ask(question),
            )

        access = authenticate_request(identity, http_request)

        caller = (
            CallerContext(access=access, conversation_id=request.conversation_id)
            if request.conversation_id
            else None
        )

        return _answer_safely(
            request_id,
            lambda: agent_for_caller(access).ask(question, caller=caller),
        )

    # ======================================================
    # Clarify (jwt mode only)
    #
    # Continues an entity clarification with the chosen option.
    # The store re-checks that the choice belongs to this user,
    # tenant and conversation, and that it has not expired.
    # ======================================================

    if identity is not None:

        @app.get(
            "/me",
            response_model=MeResponse,
            responses={401: {"model": APIErrorResponse}},
        )
        def me(http_request: Request):

            principal, tenants = identify_request(identity, http_request)

            return MeResponse(
                subject_id=f"{principal.kind}:{principal.subject_id}",
                kind=principal.kind,
                roles=list(principal.roles),
                tenants=sorted(tenants),
            )

        @app.post(
            "/clarify",
            response_model=AnswerResponse,
            responses=error_responses,
        )
        def clarify(
            request: ClarifyRequest,
            http_request: Request,
        ):

            request_id = new_request_id()

            access = authenticate_request(identity, http_request)

            caller = CallerContext(
                access=access,
                conversation_id=request.conversation_id,
            )

            reply = ClarificationResponse(
                clarification_id=request.clarification_id,
                option_id=request.option_id,
            )

            return _answer_safely(
                request_id,
                lambda: agent_for_caller(access).resume(reply, caller=caller),
            )

    if documents is not None:
        if identity is None:
            raise ValueError("Documents need AUTH_MODE=jwt: each document belongs to the caller's workspace.")
        add_document_routes(app, identity, documents, document_max_file_bytes, retriever=document_search)

    return app
