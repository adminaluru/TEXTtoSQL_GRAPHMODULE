import pytest
from decimal import Decimal

from fastapi.testclient import (
    TestClient,
)

from answering.models import (
    AnswerResponse,
)

from api.application import (
    create_app,
)


class StubResearchAgent:
    """
    Lightweight API test double.

    We are testing FastAPI transport here,
    not PostgreSQL/Neo4j again.
    """

    def ask(
        self,
        question: str,
    ) -> AnswerResponse:

        assert (
            question
            ==
            "How much has Blue Prism paid?"
        )

        return AnswerResponse(
            answer=(
                "The total payment amount "
                "for Blue Prism is 12500.00."
            ),

            result_type="scalar",

            row_count=1,

            data={
                "alias":
                    "sum_payment_amount",

                "value":
                    Decimal("12500.00"),
            },

            warnings=[],
        )


def test_ask_endpoint():

    app = create_app(
        StubResearchAgent()
    )

    client = TestClient(
        app
    )

    response = client.post(
        "/ask",
        json={
            "question":
                "How much has Blue Prism paid?"
        },
    )

    assert (
        response.status_code
        == 200
    )

    body = response.json()

    assert (
        body["result_type"]
        == "scalar"
    )

    assert (
        body["row_count"]
        == 1
    )

    assert (
        "Blue Prism"
        in body["answer"]
    )


def test_health_endpoint():

    app = create_app(
        StubResearchAgent()
    )

    client = TestClient(
        app
    )

    response = client.get(
        "/health"
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.json()
        == {
            "status": "ok"
        }
    )


def test_empty_question_rejected():

    app = create_app(
        StubResearchAgent()
    )

    client = TestClient(
        app
    )

    response = client.post(
        "/ask",
        json={
            "question": "   "
        },
    )

    assert (
        response.status_code
        == 400
    )


def test_api_key_is_required_when_configured():

    app = create_app(
        StubResearchAgent(),
        api_key="super-secret-key",
    )

    client = TestClient(
        app
    )

    missing_key_response = client.post(
        "/ask",
        json={
            "question": "How much has Blue Prism paid?"
        },
    )

    assert (
        missing_key_response.status_code
        == 401
    )

    authorized_response = client.post(
        "/ask",
        headers={
            "Authorization": "Bearer super-secret-key",
        },
        json={
            "question": "How much has Blue Prism paid?"
        },
    )

    assert (
        authorized_response.status_code
        == 200
    )


def test_rate_limit_blocks_excess_requests():

    app = create_app(
        StubResearchAgent(),
        api_key="super-secret-key",
        rate_limit_requests_per_minute=2,
    )

    client = TestClient(
        app
    )

    headers = {
        "Authorization": "Bearer super-secret-key"
    }

    first_response = client.post(
        "/ask",
        headers=headers,
        json={
            "question": "How much has Blue Prism paid?"
        },
    )

    second_response = client.post(
        "/ask",
        headers=headers,
        json={
            "question": "How much has Blue Prism paid?"
        },
    )

    third_response = client.post(
        "/ask",
        headers=headers,
        json={
            "question": "How much has Blue Prism paid?"
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert third_response.status_code == 429


class DependencyFailureAgent:
    def ask(self, question: str):
        raise ConnectionError("Database unavailable")


def test_dependency_failure_returns_503():

    app = create_app(
        DependencyFailureAgent()
    )

    client = TestClient(
        app
    )

    response = client.post(
        "/ask",
        json={
            "question": "How much has Blue Prism paid?"
        },
    )

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"].lower()


def test_startup_validation_failure_raises():

    def failing_startup_check():
        raise RuntimeError("PostgreSQL dependency unavailable")

    app = create_app(
        StubResearchAgent(),
        startup_validator=failing_startup_check,
    )

    with pytest.raises(RuntimeError, match="startup validation failed"):
        with TestClient(app):
            pass


def test_readiness_endpoints():

    app = create_app(
        StubResearchAgent()
    )

    client = TestClient(
        app
    )

    health_response = client.get(
        "/healthz"
    )

    assert (
        health_response.status_code
        == 200
    )

    ready_response = client.get(
        "/readyz"
    )

    assert (
        ready_response.status_code
        == 200
    )


# ==========================================================
# Hardening
# ==========================================================

QUESTION = {"question": "How much has Blue Prism paid?"}


def test_wrong_api_key_rejected():

    client = TestClient(
        create_app(
            StubResearchAgent(),
            api_key="super-secret-key",
        )
    )

    for headers in (
        {"Authorization": "Bearer super-secret-kex"},
        {"Authorization": "Bearer super-secret-key-extra"},
        {"X-API-Key": ""},
    ):
        response = client.post("/ask", headers=headers, json=QUESTION)
        assert response.status_code == 401

    response = client.post(
        "/ask",
        headers={"X-API-Key": "super-secret-key"},
        json=QUESTION,
    )
    assert response.status_code == 200


def test_spoofed_forwarded_for_does_not_bypass_rate_limit():

    client = TestClient(
        create_app(
            StubResearchAgent(),
            rate_limit_requests_per_minute=2,
            trusted_proxies=[],
        )
    )

    statuses = [
        client.post(
            "/ask",
            headers={"X-Forwarded-For": f"203.0.113.{index}"},
            json=QUESTION,
        ).status_code
        for index in range(3)
    ]

    assert statuses == [200, 200, 429]


def test_trusted_proxy_forwarded_for_identifies_clients():

    # TestClient's peer address is "testclient", which is not
    # an IP, so trust is exercised through the helper directly.
    from starlette.requests import Request

    from api.application import _client_key, _parse_networks

    trusted = _parse_networks(["10.0.0.0/8"])

    def request(peer: str, forwarded: str) -> Request:
        return Request({
            "type": "http",
            "client": (peer, 1234),
            "headers": [(b"x-forwarded-for", forwarded.encode())],
        })

    # Peer is a trusted proxy: rightmost untrusted hop wins.
    assert _client_key(
        request("10.0.0.5", "198.51.100.7, 10.0.0.9"),
        trusted,
    ) == "198.51.100.7"

    # Peer is not trusted: forwarding header is ignored.
    assert _client_key(
        request("198.51.100.20", "198.51.100.7"),
        trusted,
    ) == "198.51.100.20"

    # Spoofed leftmost entry cannot override the real client.
    assert _client_key(
        request("10.0.0.5", "1.2.3.4, 198.51.100.7"),
        trusted,
    ) == "198.51.100.7"


class InternalDetailAgent:
    def __init__(self, error: Exception):
        self._error = error

    def ask(self, question: str):
        raise self._error


@pytest.mark.parametrize(
    "error, status_code",
    [
        (ValueError("SQL references table(s) not present: public.secret"), 400),
        (PermissionError("Violations: public.secret join"), 403),
        (RuntimeError("Neo4j bolt://internal-host:7687 refused"), 503),
        (KeyError("public.secret"), 500),
    ],
)
def test_errors_are_generic_with_request_id(error, status_code, caplog):

    client = TestClient(
        create_app(InternalDetailAgent(error))
    )

    with caplog.at_level("WARNING", logger="uvicorn.error"):
        response = client.post("/ask", json=QUESTION)

    body = response.json()

    assert response.status_code == status_code
    assert "public.secret" not in body["detail"]
    assert "internal-host" not in body["detail"]
    assert len(body["request_id"]) == 12
    assert response.headers["X-Request-ID"] == body["request_id"]

    # Details stay available to operators via the log.
    assert any(
        body["request_id"] in record.getMessage()
        and str(error).strip("'") in record.getMessage()
        for record in caplog.records
    )


def test_readiness_reports_failing_dependency():

    def broken() -> bool:
        raise ConnectionError("down")

    client = TestClient(
        create_app(
            StubResearchAgent(),
            readiness_checks={
                "neo4j": lambda: True,
                "postgresql": lambda: False,
                "other": broken,
            },
        )
    )

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "neo4j": True,
            "postgresql": False,
            "other": False,
        },
    }


def test_readiness_ok_when_dependencies_healthy():

    client = TestClient(
        create_app(
            StubResearchAgent(),
            readiness_checks={"neo4j": lambda: True},
        )
    )

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["checks"] == {"neo4j": True}


def test_openai_client_gets_explicit_timeout(monkeypatch):

    from ai.providers.openai_chat import OpenAIChatModel

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    model = OpenAIChatModel(
        timeout_seconds=12.5,
        max_retries=0,
    )

    assert model._client.timeout == 12.5
    assert model._client.max_retries == 0
