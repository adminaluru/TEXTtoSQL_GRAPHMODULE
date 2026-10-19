"""
Step 4: the Text-to-SQL engine behind the portable Retriever contract.

Unit tests use a fake engine; integration tests use the real engine against
the Golden Data (read-only) with the synthetic tenant mapping from
test_tenant_entity_scope.py.
"""
import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from adapters.sql_retriever import (DEADLINE_EXCEEDED, FAILED, NOT_PERMITTED, UNAVAILABLE,
                                    SQLRetriever)
from answering.models import AnswerResponse
from clarification_state import ClarificationStateError
from hybrid_contracts import (AccessContext, ClarificationResponse, DisabledRetriever,
                              RetrievalRequest, RetrievalResult, Route)
from tests.rag_host_stub import answer_with

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def access(tenant="tenant-acme"):
    return AccessContext(subject_id="u1", tenant_id=tenant, policy_version="test")


def request(**kwargs):
    defaults = dict(request_id="req-1", question="How much?", access=access(), route=Route.SQL,
                    deadline_at=NOW + timedelta(seconds=30))
    defaults.update(kwargs)
    return RetrievalRequest(**defaults)


def answer(result_type, data=None, text="An answer.", warnings=(), outcome=None, rows=0):
    response = AnswerResponse(answer=text, result_type=result_type, row_count=rows, data=data,
                              warnings=list(warnings))
    response._outcome = outcome
    return response


def retriever(agent=None, error=None, clock=lambda: NOW):
    def agent_for(_access):
        if error:
            raise error
        return agent
    return SQLRetriever(agent_for, clock=clock)


def agent_returning(response):
    agent = Mock()
    agent.ask.return_value = response
    agent.resume.return_value = response
    return agent


# ----------------------------------------------------------------- mapping

def test_scalar_money_becomes_complete_evidence_with_provenance():
    agent = agent_returning(answer("scalar", {"alias": "v", "value": Decimal("4000.00"),
                                              "currency": "USD", "missing_count": 0},
                                   text="The total payment amount is 4000.00 USD."))
    result = retriever(agent).retrieve(request())
    assert result.status == "complete"
    [evidence] = result.evidence
    assert evidence.content == "The total payment amount is 4000.00 USD."
    assert (evidence.monetary_value.amount, evidence.monetary_value.currency) == (Decimal("4000.00"), "USD")
    [source] = evidence.sources
    assert (source.kind, source.tenant_id, source.source_id) == ("database", "tenant-acme",
                                                                 "flowone-sql/req-1")
    assert "SELECT" not in source.location and "erp_" not in source.location
    assert RetrievalResult.model_validate_json(result.model_dump_json()) == result


def test_count_has_no_money():
    agent = agent_returning(answer("scalar", {"alias": "c", "value": 18}))
    assert retriever(agent).retrieve(request()).evidence[0].monetary_value is None


def test_tabular_becomes_typed_table_citing_its_evidence():
    rows = [{"customer_name": "A", "currency": "USD", "total": Decimal("1.50"), "n": 2},
            {"customer_name": "B", "currency": None, "total": 0, "n": 0}]
    agent = agent_returning(answer("tabular", rows, text="Returned 2 matching rows.", rows=2))
    result = retriever(agent).retrieve(request())
    [table] = result.tables
    assert [(c.key, c.data_type, c.nullable) for c in table.columns] == [
        ("customer_name", "text", False), ("currency", "text", True),
        ("total", "decimal", False), ("n", "integer", False)]
    assert table.rows[1].cells[2].value == Decimal("0")
    assert all(row.evidence_ids == (result.evidence[0].evidence_id,) for row in table.rows)
    assert RetrievalResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("response,status", [
    (answer("empty"), "empty"),
    (answer("scalar", None), "empty"),
    (answer("clarification", outcome="clarification_unsupported"), "unsupported"),
    (answer("clarification", outcome="clarification_fanout"), "unsupported"),
    (answer("clarification", outcome="clarification_value"), "clarification"),
    (answer("clarification", outcome="clarification_entity"), "clarification"),
    (answer("clarification", outcome="clarification_unresolved"), "clarification"),
])
def test_outcomes_map_to_truthful_statuses(response, status):
    result = retriever(agent_returning(response)).retrieve(request())
    assert result.status == status
    assert not result.evidence


def test_offered_choices_are_passed_through_without_record_keys():
    prompt = {"clarification_id": "c1", "question": "Which one?", "reason": "ambiguous_entity",
              "options": [{"option_id": "o1", "label": "Blue LE-1 — Acme Primary"}],
              "allows_text": False, "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
              "contract_version": "1.0"}
    agent = agent_returning(answer("clarification", prompt, "Which one?", outcome="clarification_entity"))
    result = retriever(agent).retrieve(request(conversation_id="conv-1"))
    assert result.status == "clarification"
    assert [o.label for o in result.clarification.options] == ["Blue LE-1 — Acme Primary"]


@pytest.mark.parametrize("error,status,message", [
    (PermissionError("tenant 527 not mapped"), "denied", NOT_PERMITTED),
    (ConnectionError("db down at 10.0.0.5"), "unavailable", UNAVAILABLE),
    (TimeoutError("statement timeout"), "unavailable", UNAVAILABLE),
    (RuntimeError("SELECT * FROM secret"), "failed", FAILED),
    (ValueError("column erp_x missing"), "failed", FAILED),
])
def test_errors_are_mapped_without_leaking_detail(error, status, message):
    result = retriever(error=error).retrieve(request())
    assert (result.status, result.warnings) == (status, (message,))


def test_expired_or_foreign_choice_reports_the_safe_reason():
    agent = Mock()
    agent.resume.side_effect = ClarificationStateError("This clarification is no longer available.")
    result = retriever(agent).retrieve(request(
        conversation_id="conv-1",
        clarification_response=ClarificationResponse(clarification_id="c", option_id="o")))
    assert result.status == "failed"
    assert result.warnings == ("This clarification is no longer available.",)


def test_choice_reply_without_its_conversation_is_denied():
    agent = Mock()
    result = retriever(agent).retrieve(request(
        clarification_response=ClarificationResponse(clarification_id="c", option_id="o")))
    assert result.status == "denied"
    agent.resume.assert_not_called()


def test_conversation_binds_caller_and_absence_offers_no_choices():
    agent = agent_returning(answer("empty"))
    retriever(agent).retrieve(request(conversation_id="conv-1"))
    caller = agent.ask.call_args.kwargs["caller"]
    assert (caller.access.tenant_id, caller.conversation_id) == ("tenant-acme", "conv-1")
    retriever(agent).retrieve(request())
    assert agent.ask.call_args.kwargs["caller"] is None


def test_passed_deadline_never_starts_the_engine():
    agent = Mock()
    result = retriever(agent).retrieve(request(deadline_at=NOW))
    assert (result.status, result.warnings) == ("failed", (DEADLINE_EXCEEDED,))
    agent.ask.assert_not_called()


def test_deadline_passing_during_the_query_is_reported():
    times = iter([NOW, NOW, NOW + timedelta(seconds=31)])   # start check, deadline set-up, end check
    agent = agent_returning(answer("scalar", {"alias": "v", "value": 1}))
    result = retriever(agent, clock=lambda: next(times)).retrieve(request())
    assert result.status == "failed" and result.warnings == (DEADLINE_EXCEEDED,)


def test_wrong_route_is_rejected():
    with pytest.raises(ValueError, match="wrong retriever"):
        retriever(Mock()).retrieve(request(route=Route.DOCUMENT))


# ----------------------------------------------------------- portability

def test_rag_host_stub_depends_only_on_the_public_contract():
    tree = ast.parse(Path(__file__).with_name("rag_host_stub.py").read_text(encoding="utf-8"))
    roots = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    roots |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert roots == {"hybrid_contracts"}


def test_host_handles_sql_and_disabled_retrievers_the_same_way():
    agent = agent_returning(answer("scalar", {"alias": "v", "value": Decimal("1.00"), "currency": "USD"},
                                   text="The total is 1.00 USD."))
    _, shown = answer_with(retriever(agent), request())
    assert shown == "The total is 1.00 USD. [sources: flowone-sql/req-1]"
    _, shown = answer_with(DisabledRetriever(Route.SQL, "Structured data is switched off."), request())
    assert shown == "[disabled] Structured data is switched off."


# ------------------------------------------------------------ live engine

@pytest.fixture
def live_retriever():
    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from clarification_state import PendingSelectionStore
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor
    from planning.entity_choice_labels import load_choice_labels
    from planning.tenant_entity_scope import TenantEntityScopePolicy
    from tests.test_tenant_entity_scope import RESOLVER

    neo4j = Neo4jClient()
    store = PendingSelectionStore()
    scripted = {}

    def make(structured_response):
        scripted["response"] = structured_response

    def agent_for(caller_access):
        return build_research_agent(
            chat_model=MockChatModel(structured_response=scripted["response"]),
            neo4j_client=neo4j,
            postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100),
            entity_scope_policy=TenantEntityScopePolicy(RESOLVER, caller_access),
            entity_choice_labels=load_choice_labels(),
            clarification_store=store,
        )

    yield SQLRetriever(agent_for), make
    neo4j.close()


def claims_for(name):
    return {"intent": "aggregate", "concepts": ["Customer", "InvoiceClaim"], "result_concept": "Customer",
            "measure_concept": "InvoiceClaim", "measure_attribute": "claim amount",
            "group_by_concepts": [], "aggregation": "SUM",
            "entities": [{"concept": "Customer", "attribute": "customer name", "value": name}]}


def live_request(**kwargs):
    return request(deadline_at=datetime.now(timezone.utc) + timedelta(seconds=60), **kwargs)


@pytest.mark.integration
def test_live_scalar_answer_through_the_contract(live_retriever):
    sql, script = live_retriever
    script(claims_for("Blue Prism Technologies LE-1001"))
    result = sql.retrieve(live_request())
    assert result.status == "complete"
    money = result.evidence[0].monetary_value
    assert (money.amount, money.currency) == (Decimal("500.00"), "USD")


@pytest.mark.integration
def test_live_choose_and_resume_through_the_contract(live_retriever):
    sql, script = live_retriever
    script(claims_for("Blue Prism"))
    asked = sql.retrieve(live_request(conversation_id="conv-1"))
    assert asked.status == "clarification"
    option = next(o for o in asked.clarification.options if "LE-1002" in o.label)
    resumed = sql.retrieve(live_request(
        request_id="req-2", conversation_id="conv-1",
        clarification_response=ClarificationResponse(
            clarification_id=asked.clarification.clarification_id, option_id=option.option_id)))
    assert resumed.status == "complete"
    assert resumed.evidence[0].monetary_value.amount == Decimal("1000.00")


@pytest.mark.integration
def test_live_unmapped_tenant_is_denied(live_retriever):
    sql, script = live_retriever
    script(claims_for("Blue Prism"))
    result = sql.retrieve(live_request(access=access("tenant-unknown")))
    assert (result.status, result.warnings) == ("denied", (NOT_PERMITTED,))


@pytest.mark.integration
def test_live_multi_currency_total_is_a_table_per_currency(live_retriever):
    sql, script = live_retriever
    script({"intent": "aggregate", "concepts": ["Invoice"], "result_concept": "Invoice",
            "measure_concept": "Invoice", "measure_attribute": "invoice amount",
            "group_by_concepts": [], "entities": [], "aggregation": "SUM"})
    result = sql.retrieve(live_request())
    assert result.status == "complete"
    [table] = result.tables
    keys = [c.key for c in table.columns]
    by_currency = {row.cells[keys.index("currency")].value: row.cells[keys.index("sum_amount")].value
                   for row in table.rows}
    # Acme's invoices only (read-only SQL, company 527): USD 129000.00, INR 70800.00.
    # This test previously expected 327000.00 USD, which silently included Nova's
    # and Vertex's invoices (tenant isolation defect fixed by TenantPlanScope).
    assert by_currency == {"USD": Decimal("129000.00"), "INR": Decimal("70800.00")}


# ------------------------------------------------- /ask compatibility

def test_internal_outcome_never_reaches_the_ask_json():
    response = answer("clarification", outcome="clarification_unsupported")
    assert set(response.model_dump()) == {"answer", "result_type", "row_count", "data", "warnings"}
    assert "outcome" not in response.model_dump_json()


def test_ask_endpoint_shape_is_unchanged():
    from fastapi.testclient import TestClient

    from api.application import create_app

    agent = agent_returning(answer("clarification", text="Please rephrase.",
                                   outcome="clarification_unsupported"))
    with TestClient(create_app(agent)) as client:
        body = client.post("/ask", json={"question": "How much?"}).json()
    assert body == {"answer": "Please rephrase.", "result_type": "clarification", "row_count": 0,
                    "data": None, "warnings": []}
