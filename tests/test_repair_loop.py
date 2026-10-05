"""
Repair loop behaviour against the live ontology and
PostgreSQL. Only the LLM is scripted.

GQ001 Golden Data: total payment for Blue Prism = 24000.00
"""

from decimal import Decimal

import pytest

from fastapi.testclient import TestClient

from agent.factory import build_research_agent
from agent.repair import REPAIR_WARNING, UNSUPPORTED_MESSAGE
from ai.providers.mock_chat import MockChatModel
from api.application import create_app
from database.neo4j import Neo4jClient
from execution.postgres_executor import PostgreSQLExecutor


QUESTION = "How much has Blue Prism paid?"

GOOD = {
    "intent": "aggregate",
    "concepts": ["Customer", "PaymentReceipt"],
    "result_concept": "Customer",
    "measure_concept": "PaymentReceipt",
    "measure_attribute": "payment amount",
    "group_by_concepts": [],
    "entities": [
        {
            "concept": "Customer",
            "attribute": "customer name",
            "value": "Blue Prism",
        }
    ],
    "aggregation": "SUM",
}

# Concept that does not exist in the ontology.
UNKNOWN_CONCEPT = {
    **GOOD,
    "concepts": ["Client", "PaymentReceipt"],
    "result_concept": "Client",
    "entities": [
        {
            "concept": "Client",
            "attribute": "customer name",
            "value": "Blue Prism",
        }
    ],
}

# Valid concepts, attribute the column resolver cannot map.
UNKNOWN_ATTRIBUTE = {
    **GOOD,
    "measure_attribute": "zzqx flarb quotient",
}

# Internally inconsistent: result concept not in concepts.
INCONSISTENT = {
    **GOOD,
    "result_concept": "Invoice",
}

UNKNOWN_INTENT = {
    **GOOD,
    "intent": "unknown",
    "aggregation": None,
}


class ScriptedChatModel(MockChatModel):
    """Returns the scripted responses in order, recording requests."""

    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.requests = []

    def generate_structured(self, request, response_schema):
        self.requests.append(request)

        response = self._responses[
            min(len(self.requests), len(self._responses)) - 1
        ]

        if isinstance(response, Exception):
            raise response

        return response_schema.model_validate(response)


@pytest.fixture
def neo4j():
    client = Neo4jClient()
    yield client
    client.close()


def _agent(neo4j, model, **kwargs):
    return build_research_agent(
        chat_model=model,
        neo4j_client=neo4j,
        postgres_executor=PostgreSQLExecutor(
            statement_timeout_ms=5000,
            max_rows=100,
        ),
        **kwargs,
    )


def _total(answer):
    return Decimal(str(answer.data["value"]))


def test_first_attempt_success_has_no_warning(neo4j):

    model = ScriptedChatModel([GOOD])

    answer = _agent(neo4j, model).ask(QUESTION)

    assert _total(answer) == Decimal("24000.00")
    assert REPAIR_WARNING not in answer.warnings
    assert len(model.requests) == 1


def test_unknown_concept_is_repaired(neo4j):

    model = ScriptedChatModel([UNKNOWN_CONCEPT, GOOD])

    answer = _agent(neo4j, model).ask(QUESTION)

    assert _total(answer) == Decimal("24000.00")
    assert REPAIR_WARNING in answer.warnings
    assert len(model.requests) == 2

    # The retry carries the original question plus feedback
    # naming the rejected concept.
    retry = model.requests[1].messages

    assert retry[1].content == QUESTION
    assert retry[-1].role == "user"
    assert "Client" in retry[-1].content
    assert "Do not change what the user asked for" in retry[-1].content


def test_planning_stage_failure_is_repaired_with_previous_answer(neo4j):

    model = ScriptedChatModel([UNKNOWN_ATTRIBUTE, GOOD])

    answer = _agent(neo4j, model).ask(QUESTION)

    assert _total(answer) == Decimal("24000.00")
    assert len(model.requests) == 2

    # The model sees its own rejected analysis.
    retry = model.requests[1].messages

    assert retry[-2].role == "assistant"
    assert "zzqx flarb quotient" in retry[-2].content


def test_inconsistent_analysis_is_repaired(neo4j):

    model = ScriptedChatModel([INCONSISTENT, GOOD])

    answer = _agent(neo4j, model).ask(QUESTION)

    assert _total(answer) == Decimal("24000.00")
    assert len(model.requests) == 2


def test_exhausted_repairs_return_clarification(neo4j):

    model = ScriptedChatModel([UNKNOWN_ATTRIBUTE])

    answer = _agent(neo4j, model, max_repair_attempts=2).ask(QUESTION)

    assert answer.result_type == "clarification"
    assert answer.data is None
    assert "Customer, PaymentReceipt" in answer.answer
    assert "flarb" not in answer.answer

    # 1 attempt + 2 repairs
    assert len(model.requests) == 3


def test_unsupported_question_is_never_retried(neo4j):

    model = ScriptedChatModel([UNKNOWN_INTENT, GOOD])

    answer = _agent(neo4j, model).ask(QUESTION)

    assert answer.result_type == "clarification"
    assert answer.answer == UNSUPPORTED_MESSAGE
    assert len(model.requests) == 1


def test_repair_can_be_disabled(neo4j):

    model = ScriptedChatModel([UNKNOWN_CONCEPT, GOOD])

    answer = _agent(neo4j, model, max_repair_attempts=0).ask(QUESTION)

    assert answer.result_type == "clarification"
    assert len(model.requests) == 1


def test_budget_stops_further_attempts(neo4j):

    model = ScriptedChatModel([UNKNOWN_CONCEPT, GOOD])

    answer = _agent(
        neo4j,
        model,
        repair_budget_seconds=0,
    ).ask(QUESTION)

    assert answer.result_type == "clarification"
    assert len(model.requests) == 1


def test_infrastructure_errors_are_not_retried(neo4j):

    model = ScriptedChatModel([
        RuntimeError("OpenAI structured generation failed."),
        GOOD,
    ])

    client = TestClient(create_app(_agent(neo4j, model)))

    response = client.post("/ask", json={"question": QUESTION})

    assert response.status_code == 503
    assert len(model.requests) == 1


def test_clarification_over_http(neo4j):

    model = ScriptedChatModel([UNKNOWN_INTENT])

    client = TestClient(create_app(_agent(neo4j, model)))

    response = client.post("/ask", json={"question": QUESTION})

    assert response.status_code == 200
    assert response.json() == {
        "answer": UNSUPPORTED_MESSAGE,
        "result_type": "clarification",
        "row_count": 0,
        "data": None,
        "warnings": [],
    }
