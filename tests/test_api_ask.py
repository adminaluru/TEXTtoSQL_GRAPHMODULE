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
