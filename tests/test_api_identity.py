"""
Step 5: verified caller identity on the API (AUTH_MODE=jwt).

Done-when (handoff): missing / expired / invalid credentials and spoofed
membership are rejected; identity consistently reaches policy and retrieval.
"""
import time
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from answering.models import AnswerResponse
from api.application import create_app
from api.identity_setup import (identity_settings_from, require_safe_auth_mode,
                                tenant_resolver_from_file)
from clarification_state import ClarificationStateError
from trusted_identity import ClaimMapping, IdentitySettings, IdentityVerifier, issue_dev_token

SECRET = "dev-secret-for-tests-only-0123456789abcdef"
SETTINGS = IdentitySettings(environment="development", issuer="flowone-dev", audience="flowone-api",
                            algorithms=("HS256",), shared_secret=SECRET, policy_version="dev-1",
                            claims=ClaimMapping(principal_kind="kind"))
EXAMPLE_SCOPE = str(Path(__file__).parents[1] / "docs" / "examples" / "tenant_scope.dev.json")


def token(tenants=("tenant-acme",), subject="alice", **kwargs):
    return issue_dev_token(SETTINGS, subject=subject, tenants=list(tenants), **kwargs)


def headers(bearer=None, tenant=None):
    values = {}
    if bearer:
        values["Authorization"] = f"Bearer {bearer}"
    if tenant:
        values["X-FlowOne-Tenant"] = tenant
    return values


def fake_agent(result=None, error=None):
    agent = Mock()
    response = result or AnswerResponse(answer="ok", result_type="empty", row_count=0)
    agent.ask.side_effect = error or (lambda *a, **k: response)
    agent.resume.side_effect = error or (lambda *a, **k: response)
    return agent


def jwt_client(agent):
    seen = []

    def agent_for(access):
        seen.append(access)
        return agent

    app = create_app(Mock(), identity=IdentityVerifier(SETTINGS), agent_for=agent_for,
                     rate_limit_requests_per_minute=1000)
    return TestClient(app), seen


GENERIC_401 = "Authentication is required. The access token is missing, invalid or expired."


# ------------------------------------------------------------ authentication

@pytest.mark.parametrize("bearer", [
    None,
    "garbage",
    jwt.encode({"iss": "flowone-dev", "aud": "flowone-api", "sub": "alice", "tenants": ["tenant-acme"],
                "iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600}, SECRET, algorithm="HS256"),
    jwt.encode({"iss": "flowone-dev", "aud": "flowone-api", "sub": "alice", "tenants": ["tenant-acme"],
                "iat": int(time.time()), "exp": int(time.time()) + 600},
               "attacker-secret-0123456789abcdefghijk", algorithm="HS256"),
])
def test_missing_invalid_or_expired_tokens_get_401(bearer):
    agent = fake_agent()
    client, seen = jwt_client(agent)
    with client:
        response = client.post("/ask", json={"question": "How much?"}, headers=headers(bearer))
    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert not seen and not agent.ask.called


def test_shared_api_key_is_not_accepted_in_jwt_mode():
    client, seen = jwt_client(fake_agent())
    with client:
        response = client.post("/ask", json={"question": "Q"}, headers={"X-API-Key": "anything"})
    assert response.status_code == 401 and not seen


# ------------------------------------------------------------ tenant selection

def test_spoofed_tenant_header_gets_403():
    client, seen = jwt_client(fake_agent())
    with client:
        response = client.post("/ask", json={"question": "Q"},
                               headers=headers(token(), tenant="tenant-nova"))
    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to the selected company."
    assert not seen


def test_several_companies_require_the_tenant_header():
    client, seen = jwt_client(fake_agent())
    with client:
        missing = client.post("/ask", json={"question": "Q"},
                              headers=headers(token(("tenant-acme", "tenant-nova"))))
        chosen = client.post("/ask", json={"question": "Q"},
                             headers=headers(token(("tenant-acme", "tenant-nova")), "tenant-nova"))
    assert missing.status_code == 403 and missing.json()["detail"] == "Please choose which company to use."
    assert chosen.status_code == 200
    assert [a.tenant_id for a in seen] == ["tenant-nova"]


def test_tenant_in_the_body_is_rejected_not_trusted():
    client, seen = jwt_client(fake_agent())
    with client:
        response = client.post("/ask", json={"question": "Q", "tenant_id": "tenant-nova"},
                               headers=headers(token()))
    assert response.status_code == 422 and not seen


# ----------------------------------------------- identity reaches the agent

def test_verified_identity_and_conversation_reach_the_agent():
    agent = fake_agent()
    client, seen = jwt_client(agent)
    with client:
        response = client.post("/ask", json={"question": " How much? ", "conversation_id": "conv-1"},
                               headers=headers(token(roles=["sales"])))
    assert response.status_code == 200
    [access] = seen
    assert (access.subject_id, access.tenant_id, access.roles, access.policy_version) == (
        "user:alice", "tenant-acme", ("sales",), "dev-1")
    caller = agent.ask.call_args.kwargs["caller"]
    assert (caller.access, caller.conversation_id) == (access, "conv-1")
    assert agent.ask.call_args.args == ("How much?",)


def test_without_conversation_no_choices_can_be_bound():
    agent = fake_agent()
    client, _ = jwt_client(agent)
    with client:
        client.post("/ask", json={"question": "Q"}, headers=headers(token()))
    assert agent.ask.call_args.kwargs["caller"] is None


def test_service_tokens_are_identified_as_services():
    client, seen = jwt_client(fake_agent())
    with client:
        client.post("/ask", json={"question": "Q"}, headers=headers(token(subject="batch", service=True)))
    assert seen[0].subject_id == "service:batch"


def test_clarify_rejects_foreign_or_expired_choice_with_409():
    agent = fake_agent(error=ClarificationStateError("This clarification is no longer available."))
    client, _ = jwt_client(agent)
    with client:
        response = client.post("/clarify", json={"conversation_id": "c", "clarification_id": "x",
                                                 "option_id": "y"}, headers=headers(token()))
    assert response.status_code == 409
    assert response.json()["detail"] == "This clarification is no longer available."


def test_clarify_requires_a_token():
    client, _ = jwt_client(fake_agent())
    with client:
        response = client.post("/clarify", json={"conversation_id": "c", "clarification_id": "x",
                                                 "option_id": "y"})
    assert response.status_code == 401


# ------------------------------------------------- api_key mode is unchanged

def test_api_key_mode_has_no_clarify_and_calls_ask_as_before():
    agent = Mock()
    agent.ask.return_value = AnswerResponse(answer="ok", result_type="empty", row_count=0)
    with TestClient(create_app(agent, api_key="k")) as client:
        assert client.post("/clarify", json={}).status_code == 404
        response = client.post("/ask", json={"question": "Q", "conversation_id": "c"},
                               headers={"X-API-Key": "k"})
    assert response.status_code == 200
    assert agent.ask.call_args.args == ("Q",) and not agent.ask.call_args.kwargs


# ------------------------------------------------------------ configuration

def config(**overrides):
    values = dict(app_env="dev", auth_mode="jwt", identity_issuer="flowone-dev",
                  identity_audience="flowone-api", identity_algorithms=["HS256"],
                  identity_shared_secret=SECRET, identity_jwks_url=None,
                  identity_entra_directory_id=None, identity_policy_version="dev-1",
                  identity_subject_claim="sub", identity_tenants_claim="tenants",
                  identity_roles_claim="roles", identity_kind_claim="kind")
    values.update(overrides)
    if isinstance(values["identity_shared_secret"], str):
        from pydantic import SecretStr
        values["identity_shared_secret"] = SecretStr(values["identity_shared_secret"])
    return SimpleNamespace(**values)


@pytest.mark.parametrize("env", ["production", "PROD", "live"])
def test_production_refuses_the_shared_api_key(env):
    with pytest.raises(RuntimeError, match="AUTH_MODE=jwt"):
        require_safe_auth_mode(config(app_env=env, auth_mode="api_key"))


def test_development_may_use_the_api_key():
    require_safe_auth_mode(config(auth_mode="api_key"))


def test_production_refuses_a_shared_secret():
    with pytest.raises(ValidationError, match="Production requires"):
        identity_settings_from(config(app_env="production"))


def test_entra_is_configuration_only():
    settings = identity_settings_from(config(app_env="production", identity_shared_secret=None,
                                             identity_entra_directory_id="dir-123",
                                             identity_audience="api://flowone",
                                             identity_subject_claim="oid", identity_kind_claim="idtyp"))
    assert settings.jwks_url == "https://login.microsoftonline.com/dir-123/discovery/v2.0/keys"
    assert (settings.claims.subject, settings.claims.principal_kind) == ("oid", "idtyp")


def test_dev_settings_from_config_verify_dev_tokens():
    verifier = IdentityVerifier(identity_settings_from(config()))
    assert verifier.authenticate(token()).tenant_id == "tenant-acme"


def test_tenant_scope_file_is_required_and_loads():
    with pytest.raises(RuntimeError, match="TENANT_SCOPE_FILE"):
        tenant_resolver_from_file(None)
    from hybrid_contracts import AccessContext
    resolver = tenant_resolver_from_file(EXAMPLE_SCOPE)
    access = AccessContext(subject_id="u", tenant_id="tenant-nova", policy_version="1")
    assert resolver.predicates_for(access, "public.erp_customer_details")[0].value == 528


# ----------------------------------------------------------- live, read-only

@pytest.fixture
def live_client():
    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from clarification_state import PendingSelectionStore
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor
    from planning.entity_choice_labels import load_choice_labels
    from planning.tenant_entity_scope import TenantEntityScopePolicy

    neo4j = Neo4jClient()
    resolver = tenant_resolver_from_file(EXAMPLE_SCOPE)
    store = PendingSelectionStore()
    script = {}

    def agent_for(access):
        return build_research_agent(
            chat_model=MockChatModel(structured_response=script["response"]), neo4j_client=neo4j,
            postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100),
            entity_scope_policy=TenantEntityScopePolicy(resolver, access),
            entity_choice_labels=load_choice_labels(), clarification_store=store)

    app = create_app(Mock(), identity=IdentityVerifier(SETTINGS), agent_for=agent_for,
                     rate_limit_requests_per_minute=1000)
    with TestClient(app) as client:
        yield client, script
    neo4j.close()


def claims_question(name):
    return {"intent": "aggregate", "concepts": ["Customer", "InvoiceClaim"], "result_concept": "Customer",
            "measure_concept": "InvoiceClaim", "measure_attribute": "claim amount",
            "group_by_concepts": [], "aggregation": "SUM",
            "entities": [{"concept": "Customer", "attribute": "customer name", "value": name}]}


@pytest.mark.integration
def test_live_token_to_choice_to_answer(live_client):
    client, script = live_client
    script["response"] = claims_question("Blue Prism")
    asked = client.post("/ask", json={"question": "Claims for Blue Prism?", "conversation_id": "conv-1"},
                        headers=headers(token())).json()
    assert asked["result_type"] == "clarification"
    labels = [o["label"] for o in asked["data"]["options"]]
    assert labels == ["Blue Prism Technologies LE-1001 — Acme Technologies - Primary Legal Entity",
                      "Blue Prism Technologies LE-1002 — Acme Technologies - Secondary Legal Entity"]
    option = asked["data"]["options"][1]["option_id"]
    reply = {"conversation_id": "conv-1", "clarification_id": asked["data"]["clarification_id"],
             "option_id": option}

    # Another company's user cannot use Acme's pending choice.
    foreign = client.post("/clarify", json=reply, headers=headers(token(("tenant-nova",), subject="bob")))
    assert foreign.status_code == 409

    answered = client.post("/clarify", json=reply, headers=headers(token())).json()
    assert Decimal(str(answered["data"]["value"])) == Decimal("1000.00")
    assert answered["data"]["currency"] == "USD"

    replay = client.post("/clarify", json=reply, headers=headers(token()))
    assert replay.status_code == 409


@pytest.mark.integration
def test_live_other_company_cannot_see_acme_customer(live_client):
    client, script = live_client
    script["response"] = claims_question("Blue Prism Technologies LE-1001")
    acme = client.post("/ask", json={"question": "Q"}, headers=headers(token())).json()
    nova = client.post("/ask", json={"question": "Q"},
                       headers=headers(token(("tenant-nova",), subject="bob"))).json()
    assert Decimal(str(acme["data"]["value"])) == Decimal("500.00")
    assert nova["result_type"] == "clarification" and "could not find" in nova["answer"]


# ------------------------------------------------------- /me and CORS (A13.1)

def test_me_returns_verified_identity_and_companies():
    client, seen = jwt_client(fake_agent())
    with client:
        body = client.get("/me", headers=headers(token(("tenant-nova", "tenant-acme"),
                                                       roles=["credit_manager"]))).json()
    assert body == {"subject_id": "user:alice", "kind": "user", "roles": ["credit_manager"],
                    "tenants": ["tenant-acme", "tenant-nova"]}
    assert not seen          # no agent is built just to say who you are


def test_me_requires_a_valid_token():
    client, _ = jwt_client(fake_agent())
    with client:
        assert client.get("/me").status_code == 401
        assert client.get("/me", headers=headers("garbage")).status_code == 401


def test_me_does_not_exist_in_api_key_mode():
    with TestClient(create_app(Mock(), api_key="k")) as client:
        assert client.get("/me", headers={"X-API-Key": "k"}).status_code == 404


def preflight(app, origin):
    with TestClient(app) as client:
        return client.options("/ask", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                               "Access-Control-Request-Headers": "authorization,content-type"})


def test_cross_origin_access_is_closed_by_default():
    response = preflight(create_app(Mock()), "https://evil.example")
    assert "access-control-allow-origin" not in response.headers


def test_only_listed_origins_are_allowed():
    app = create_app(Mock(), cors_allowed_origins=["https://flowone.example.com"])
    allowed = preflight(app, "https://flowone.example.com")
    denied = preflight(app, "https://evil.example")
    assert allowed.headers["access-control-allow-origin"] == "https://flowone.example.com"
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in denied.headers


def test_wildcard_origin_is_refused():
    with pytest.raises(ValueError, match="not allowed"):
        create_app(Mock(), cors_allowed_origins=["*"])


# ------------------------------------------------------------ rate limit per caller

def limited_client(limit=2):
    app = create_app(Mock(), identity=IdentityVerifier(SETTINGS), agent_for=lambda access: fake_agent(),
                     rate_limit_requests_per_minute=limit)
    return TestClient(app)


def ask(client, bearer):
    return client.post("/ask", json={"question": "Q"}, headers=headers(bearer)).status_code


def test_callers_behind_one_address_have_their_own_limits():
    with limited_client() as client:
        alice, bob = token(subject="alice"), token(subject="bob")
        assert [ask(client, alice) for _ in range(3)] == [200, 200, 429]
        assert ask(client, bob) == 200


def test_new_tokens_for_the_same_caller_share_one_limit():
    with limited_client() as client:
        assert [ask(client, token(subject="alice", ttl_seconds=600 + i)) for i in range(3)] == [200, 200, 429]


def test_requests_without_a_valid_token_are_limited_by_address():
    with limited_client() as client:
        assert [ask(client, b) for b in (None, "not-a-token", None)] == [401, 401, 429]
        # A verified caller from the same address is not blocked by them.
        assert ask(client, token()) == 200


def test_clarification_answers_count_towards_the_limit():
    with limited_client(limit=1) as client:
        assert ask(client, token()) == 200
        response = client.post("/clarify", json={"conversation_id": "c", "clarification_id": "x",
                                                 "option_id": "y"}, headers=headers(token()))
        assert response.status_code == 429
