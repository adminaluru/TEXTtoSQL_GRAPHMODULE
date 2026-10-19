"""
Decisions D-001 / D-002 against the live ontology and PostgreSQL.

Logical tenants map to one company each through a synthetic test mapping.
Tenant IDs are deliberately unlike the physical company keys. Read-only.

Verified Golden Data (read-only SQL, 2026-10-14):

    company 527 owns Blue Prism Technologies LE-1001 and LE-1002
    company 528 owns Blue Prism Technologies LE-1003 and LE-1004
    SYN-CUST-1001-01#527#1001 payment total = 4000.00
"""

from decimal import Decimal

import pytest

from agent.factory import build_research_agent
from ai.providers.mock_chat import MockChatModel
from database.neo4j import Neo4jClient
from execution.postgres_executor import PostgreSQLExecutor
from pathlib import Path

from api.identity_setup import tenant_resolver_from_file
from hybrid_contracts import AccessContext
from tenant_scope import TenantScopeError

from planning.tenant_entity_scope import TenantEntityScopePolicy

CUSTOMERS = "public.erp_customer_details"

# The same development mapping the application uses (every table, plus
# claims owned through their invoice): one source of truth for tests.
RESOLVER = tenant_resolver_from_file(
    str(Path(__file__).parents[1] / "docs" / "examples" / "tenant_scope.dev.json"))


def payment_question(name):
    return {
        "intent": "aggregate",
        "concepts": ["Customer", "PaymentReceipt"],
        "result_concept": "Customer",
        "measure_concept": "PaymentReceipt",
        "measure_attribute": "payment amount",
        "group_by_concepts": [],
        "entities": [
            {"concept": "Customer", "attribute": "customer name", "value": name}
        ],
        "aggregation": "SUM",
    }


@pytest.fixture
def neo4j():
    client = Neo4jClient()
    yield client
    client.close()


def ask(neo4j, tenant, name):
    access = AccessContext(subject_id="test-user", tenant_id=tenant, policy_version="test")
    agent = build_research_agent(
        chat_model=MockChatModel(structured_response=payment_question(name)),
        neo4j_client=neo4j,
        postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100),
        entity_scope_policy=TenantEntityScopePolicy(RESOLVER, access),
    )
    return agent.ask(f"How much has {name} paid?")


def test_adapter_binds_tenant_owner_as_equality_filter():
    access = AccessContext(subject_id="u", tenant_id="tenant-nova", policy_version="test")
    [scope] = TenantEntityScopePolicy(RESOLVER, access).filters_for(CUSTOMERS)
    assert (scope.table, scope.column, scope.operator, scope.value) == (
        CUSTOMERS, "company_id", "=", 528)


@pytest.mark.integration
def test_ambiguous_name_within_active_company_requests_clarification(neo4j):
    response = ask(neo4j, "tenant-acme", "Blue Prism")
    assert response.result_type == "clarification"
    assert "more than one record" in response.answer
    assert response.data is None


@pytest.mark.integration
def test_unique_customer_in_active_company_is_answered(neo4j):
    response = ask(neo4j, "tenant-acme", "Blue Prism Technologies LE-1001")
    assert response.result_type == "scalar"
    assert Decimal(str(response.data["value"])) == Decimal("4000.00")


@pytest.mark.integration
def test_customer_owned_by_another_company_is_not_found(neo4j):
    response = ask(neo4j, "tenant-nova", "Blue Prism Technologies LE-1001")
    assert response.result_type == "clarification"
    assert "could not find" in response.answer
    assert "4000" not in response.answer


@pytest.mark.integration
def test_unmapped_tenant_fails_closed_before_lookup(neo4j):
    with pytest.raises(TenantScopeError):
        ask(neo4j, "tenant-unknown", "Blue Prism")
