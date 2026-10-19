"""
A2.2: entity clarification choices and resume (decisions D-001, D-002, D-003).

Unit tests use doubles. Integration tests run read-only against the live
ontology and PostgreSQL with a synthetic tenant mapping.

Verified Golden Data (read-only SQL, 2026-10-15):

    company 527: Blue Prism Technologies LE-1001 (Acme Technologies - Primary
                 Legal Entity), claims 500.00
                 Blue Prism Technologies LE-1002 (Acme Technologies - Secondary
                 Legal Entity), claims 1000.00
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.orchestrator import CallerContext, ResearchAgentOrchestrator
from clarification_state import ClarificationStateError, PendingSelectionStore
from hybrid_contracts import AccessContext, ClarificationResponse
from planning.entity_match_guard import EntityCandidate, EntityMatchResult
from planning.models import QueryFilter, QueryPlan


def caller(subject="u1", tenant="tenant-acme", conversation="c1"):
    return CallerContext(
        access=AccessContext(subject_id=subject, tenant_id=tenant, policy_version="test"),
        conversation_id=conversation)


def plan():
    return QueryPlan(intent="aggregate", tables=["public.customers"], filters=[
        QueryFilter(table="public.customers", column="name", operator="ILIKE", value="%Blue%")])


AMBIGUOUS = EntityMatchResult(
    plan(), "ambiguous",
    (EntityCandidate((1,), "Blue LE-1 — Acme Primary"),
     EntityCandidate((2,), "Blue LE-2 — Acme Secondary")),
    (0, "public.customers"))


def agent_with(*guard_results, store=True):
    agent = ResearchAgentOrchestrator.__new__(ResearchAgentOrchestrator)
    agent._interpret_with_repair = Mock(return_value=SimpleNamespace(query_plan=plan(), graph_plan=None))
    agent._currency_splitter = None
    agent._empty_group_policy = None
    agent._missing_value_policy = None
    agent._value_domain_guard = None
    agent._fanout_guard = Mock()
    agent._fanout_guard.find.return_value = []
    agent._entity_match_guard = Mock()
    agent._entity_match_guard.resolve.side_effect = list(guard_results)
    agent._clarification_store = PendingSelectionStore() if store else None
    agent._execute = Mock(return_value=SimpleNamespace(warnings=[]))
    return agent


def choose(prompt, label):
    option = next(o for o in prompt["options"] if o["label"] == label)
    return ClarificationResponse(clarification_id=prompt["clarification_id"],
                                 option_id=option["option_id"])


def test_identified_caller_gets_labelled_choices_without_record_keys():
    agent = agent_with(AMBIGUOUS)
    response = agent.ask("What has Blue paid?", caller=caller())
    assert response.result_type == "clarification"
    assert [o["label"] for o in response.data["options"]] == [
        "Blue LE-1 — Acme Primary", "Blue LE-2 — Acme Secondary"]
    assert agent._entity_match_guard.resolve.call_args.kwargs["list_choices"] is True
    agent._execute.assert_not_called()


def test_anonymous_caller_gets_text_guidance_only():
    agent = agent_with(EntityMatchResult(plan(), "ambiguous"))
    response = agent.ask("What has Blue paid?")
    assert response.data is None
    assert agent._entity_match_guard.resolve.call_args.kwargs["list_choices"] is False


def test_resume_rechecks_choice_and_executes_without_reinterpreting():
    pinned = plan()
    agent = agent_with(AMBIGUOUS, EntityMatchResult(pinned))
    prompt = agent.ask("What has Blue paid?", caller=caller()).data
    answer = agent.resume(choose(prompt, "Blue LE-2 — Acme Secondary"), caller=caller())
    assert answer is agent._execute.return_value
    resumed = agent._entity_match_guard.resolve.call_args
    assert resumed.kwargs["selections"] == {(0, "public.customers"): (2,)}
    assert resumed.args[0] == plan()          # original, unpinned plan
    assert agent._execute.call_args.kwargs["interpretation"].query_plan is pinned
    assert agent._interpret_with_repair.call_count == 1
    # The answer names the chosen record, not the ambiguous typed name.
    assert agent._execute.call_args.kwargs["chosen_labels"] == ("Blue LE-2 — Acme Secondary",)


def test_second_choice_keeps_the_first_chosen_label():
    second = EntityMatchResult(
        plan(), "ambiguous",
        (EntityCandidate((7,), "Nova N-1 — Acme Primary"),
         EntityCandidate((8,), "Nova N-2 — Acme Secondary")),
        (0, "public.invoices"))
    agent = agent_with(AMBIGUOUS, second, EntityMatchResult(plan()))
    first = agent.ask("What has Blue paid on Nova invoices?", caller=caller()).data
    then = agent.resume(choose(first, "Blue LE-1 — Acme Primary"), caller=caller()).data
    agent.resume(choose(then, "Nova N-2 — Acme Secondary"), caller=caller())
    assert agent._entity_match_guard.resolve.call_args.kwargs["selections"] == {
        (0, "public.customers"): (1,), (0, "public.invoices"): (8,)}
    assert agent._execute.call_args.kwargs["chosen_labels"] == (
        "Blue LE-1 — Acme Primary", "Nova N-2 — Acme Secondary")


@pytest.mark.parametrize("other", [caller(subject="u2"), caller(tenant="tenant-nova"),
                                   caller(conversation="c2")])
def test_resume_by_another_caller_is_refused(other):
    agent = agent_with(AMBIGUOUS)
    prompt = agent.ask("What has Blue paid?", caller=caller()).data
    with pytest.raises(ClarificationStateError):
        agent.resume(choose(prompt, "Blue LE-1 — Acme Primary"), caller=other)
    agent._execute.assert_not_called()


def test_resume_when_choice_is_no_longer_permitted_does_not_execute():
    agent = agent_with(AMBIGUOUS, EntityMatchResult(plan(), "The selected record is no longer available."))
    prompt = agent.ask("What has Blue paid?", caller=caller()).data
    answer = agent.resume(choose(prompt, "Blue LE-1 — Acme Primary"), caller=caller())
    assert answer.result_type == "clarification"
    agent._execute.assert_not_called()


def test_resume_without_store_is_refused():
    agent = agent_with(store=False)
    with pytest.raises(ValueError, match="not enabled"):
        agent.resume(ClarificationResponse(clarification_id="x", option_id="y"), caller=caller())


# ==========================================================
# Live, read-only
# ==========================================================

def live_agent(neo4j, store):
    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from execution.postgres_executor import PostgreSQLExecutor
    from planning.entity_choice_labels import load_choice_labels
    from planning.tenant_entity_scope import TenantEntityScopePolicy
    from tests.test_tenant_entity_scope import RESOLVER

    response = {
        "intent": "aggregate",
        "concepts": ["Customer", "InvoiceClaim"],
        "result_concept": "Customer",
        "measure_concept": "InvoiceClaim",
        "measure_attribute": "claim amount",
        "group_by_concepts": [],
        "entities": [{"concept": "Customer", "attribute": "customer name", "value": "Blue Prism"}],
        "aggregation": "SUM",
    }
    return build_research_agent(
        chat_model=MockChatModel(structured_response=response),
        neo4j_client=neo4j,
        postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100),
        entity_scope_policy=TenantEntityScopePolicy(RESOLVER, caller().access),
        entity_choice_labels=load_choice_labels(),
        clarification_store=store,
    )


LE_1001 = "Blue Prism Technologies LE-1001 — Acme Technologies - Primary Legal Entity"
LE_1002 = "Blue Prism Technologies LE-1002 — Acme Technologies - Secondary Legal Entity"


@pytest.fixture
def neo4j():
    from database.neo4j import Neo4jClient
    client = Neo4jClient()
    yield client
    client.close()


@pytest.mark.integration
@pytest.mark.parametrize("label,total", [(LE_1001, "500.00"), (LE_1002, "1000.00")])
def test_live_choice_lists_only_active_company_and_answers_the_chosen_record(neo4j, label, total):
    agent = live_agent(neo4j, PendingSelectionStore())
    prompt = agent.ask("What is the total claim amount for Blue Prism?", caller=caller())
    assert prompt.result_type == "clarification"
    assert [o["label"] for o in prompt.data["options"]] == [LE_1001, LE_1002]
    assert "SYN-CUST" not in prompt.model_dump_json()

    answer = agent.resume(choose(prompt.data, label), caller=caller())
    assert answer.result_type == "scalar"
    assert Decimal(str(answer.data["value"])) == Decimal(total)
    assert f"for {label} is" in answer.answer


@pytest.mark.integration
def test_live_replay_and_cross_tenant_resume_are_refused(neo4j):
    agent = live_agent(neo4j, PendingSelectionStore())
    prompt = agent.ask("What is the total claim amount for Blue Prism?", caller=caller()).data
    with pytest.raises(ClarificationStateError):
        agent.resume(choose(prompt, LE_1001), caller=caller(tenant="tenant-nova"))
    agent.resume(choose(prompt, LE_1001), caller=caller())
    with pytest.raises(ClarificationStateError):
        agent.resume(choose(prompt, LE_1001), caller=caller())
