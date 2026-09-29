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