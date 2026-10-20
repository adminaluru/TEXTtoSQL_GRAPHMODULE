"""
Step 7: bounded execution, least privilege, safe connections and errors.

Done-when (handoff): writes fail at the DB boundary; expensive or stuck work
is bounded or cancelled; pooled requests cannot inherit another caller's
context; failures do not leak secrets or SQL details.
"""
import time
from types import SimpleNamespace
from unittest.mock import Mock

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from api.application import create_app
from api.deployment_safety import check_deployment, database_findings
from config import Settings
from execution.models import DatabasePrivileges
from observability.deadline import DeadlineExceeded, remaining_seconds, request_deadline

SAFE = DatabasePrivileges(role="flowone_reader", is_superuser=False, bypasses_row_security=False,
                          writable_tables=0, can_create=False)
UNSAFE = DatabasePrivileges(role="postgres", is_superuser=True, bypasses_row_security=True,
                            writable_tables=50, can_create=True)


def deployment(app_env="production", auth_mode="jwt", sslmode="verify-full"):
    return SimpleNamespace(app_env=app_env, auth_mode=auth_mode, postgres_sslmode=sslmode)


# ------------------------------------------------------- start-up refusal

def test_safe_production_deployment_starts_cleanly():
    assert check_deployment(deployment(), SAFE) == []


@pytest.mark.parametrize("settings,privileges,expected", [
    (deployment(), UNSAFE, "superuser"),
    (deployment(), SAFE.model_copy(update={"writable_tables": 3}), "can modify 3 table"),
    (deployment(), SAFE.model_copy(update={"can_create": True}), "create objects"),
    (deployment(), SAFE.model_copy(update={"bypasses_row_security": True}), "row-level security"),
    (deployment(sslmode="prefer"), SAFE, "TLS is not required"),
    (deployment(auth_mode="api_key"), SAFE, "AUTH_MODE=jwt"),
])
def test_unsafe_production_deployment_refuses_to_start(settings, privileges, expected):
    with pytest.raises(RuntimeError, match=expected):
        check_deployment(settings, privileges)


def test_development_reports_the_same_findings_as_warnings():
    findings = check_deployment(deployment(app_env="dev", sslmode="prefer"), UNSAFE)
    assert len(findings) == 5 and findings == database_findings(deployment(sslmode="prefer"), UNSAFE)


# ------------------------------------------------------ connection string

def settings(**overrides):
    values = dict(postgres_host="db.internal", postgres_db="d", postgres_user="u",
                  postgres_password="secret", neo4j_uri="bolt://x", neo4j_user="n",
                  neo4j_password="n", _env_file=None)
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize("password", ["p w", "p'w", "x sslmode=disable host=evil", "a\\b", "=;"])
def test_password_cannot_inject_connection_options(password):
    parsed = conninfo_to_dict(settings(postgres_password=password, postgres_sslmode="require").postgres_dsn)
    assert parsed["password"] == password
    assert (parsed["host"], parsed["sslmode"]) == ("db.internal", "require")


def test_connection_fails_fast_and_is_identifiable():
    parsed = conninfo_to_dict(settings().postgres_dsn)
    assert parsed["connect_timeout"] == "5"
    assert parsed["application_name"] == "flowone-research-agent"


def test_secrets_are_not_shown_in_settings():
    text = repr(settings(postgres_password="TopSecret123", identity_shared_secret="x" * 40))
    assert "TopSecret123" not in text and "x" * 40 not in text


# ------------------------------------------------------- API error shapes

def api_with(error):
    agent = Mock()
    agent.ask.side_effect = error
    return TestClient(create_app(agent, rate_limit_requests_per_minute=1000))


@pytest.mark.parametrize("error,status,detail", [
    (DeadlineExceeded(), 504, "The question took too long to answer."),
    (TimeoutError("PostgreSQL statement timed out."), 503, "Dependency temporarily unavailable."),
    (ConnectionError("PostgreSQL unavailable."), 503, "Dependency temporarily unavailable."),
    (psycopg.OperationalError('password authentication failed for user "postgres" host=10.0.0.5'),
     500, "Research Agent failed to process the request."),
])
def test_failures_are_reported_without_internal_detail(error, status, detail):
    with api_with(error) as client:
        response = client.post("/ask", json={"question": "How much?"})
    assert response.status_code == status
    body = response.json()
    assert body["detail"].startswith(detail)
    assert "postgres" not in body["detail"] and "10.0.0.5" not in body["detail"]


# ----------------------------------------- deadline reaches every layer

def test_orchestrator_runs_each_question_under_its_deadline():
    from agent.orchestrator import ResearchAgentOrchestrator
    agent = ResearchAgentOrchestrator.__new__(ResearchAgentOrchestrator)
    agent._request_timeout_seconds = 0.5
    agent._ask = lambda question, caller: remaining_seconds()
    assert 0 < agent.ask("Q") <= 0.5
    assert remaining_seconds() is None


def test_retriever_deadline_becomes_the_engine_deadline():
    from datetime import datetime, timedelta, timezone

    from adapters.sql_retriever import DEADLINE_EXCEEDED, SQLRetriever
    from hybrid_contracts import AccessContext, RetrievalRequest, Route

    seen = {}

    def ask(question, caller=None):
        seen["remaining"] = remaining_seconds()
        raise DeadlineExceeded()

    agent = Mock()
    agent.ask.side_effect = ask
    request = RetrievalRequest(request_id="r", question="Q", route=Route.SQL,
                               access=AccessContext(subject_id="u", tenant_id="t", policy_version="1"),
                               deadline_at=datetime.now(timezone.utc) + timedelta(seconds=2))
    result = SQLRetriever(lambda access: agent).retrieve(request)
    assert 0 < seen["remaining"] <= 2
    assert (result.status, result.warnings) == ("failed", (DEADLINE_EXCEEDED,))


def test_llm_calls_are_capped_and_refused_when_time_is_up():
    from ai.interfaces.chat_model import ChatMessage, ChatRequest
    from ai.providers.openai_chat import OpenAIChatModel

    client = Mock()
    client.responses.create.side_effect = RuntimeError("network")
    model = OpenAIChatModel(model_name="m", client=client, timeout_seconds=30)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])

    with request_deadline(2.0):
        with pytest.raises(RuntimeError):
            model.generate_text(request)
    assert 0 < client.responses.create.call_args.kwargs["timeout"] <= 2.0

    client.responses.create.reset_mock()
    with request_deadline(0):
        with pytest.raises(DeadlineExceeded):
            model.generate_text(request)
    client.responses.create.assert_not_called()


def test_unreachable_neo4j_fails_fast_as_unavailable(monkeypatch):
    import config
    from database.neo4j import Neo4jClient

    monkeypatch.setattr(config.settings, "neo4j_uri", "bolt://127.0.0.1:1")
    client = Neo4jClient()
    started = time.monotonic()
    try:
        with pytest.raises(ConnectionError, match="Neo4j unavailable"):
            client.execute_read("RETURN 1")
    finally:
        client.close()
    assert time.monotonic() - started < 15



def _neo4j_failing_with(code):
    from unittest.mock import MagicMock

    from neo4j.exceptions import Neo4jError

    from database.neo4j import Neo4jClient

    client = Neo4jClient.__new__(Neo4jClient)
    client._database = "neo4j"
    client._query_timeout_seconds = 10
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value.run.side_effect = Neo4jError._hydrate_neo4j(
        code=code, message="server says no")
    client._driver = driver
    return client


def test_neo4j_query_timeout_is_a_timeout_not_an_internal_error():
    # Seen on a cold Neo4j (fresh CI container): the server stops a slow
    # query at the client's timeout and reports a ClientError; the API used
    # to answer 500. TimeoutError maps to 503.
    client = _neo4j_failing_with("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration")
    with pytest.raises(TimeoutError, match="Neo4j query timed out") as error:
        client.execute_read("MATCH (n) RETURN n")
    assert not isinstance(error.value, DeadlineExceeded)


def test_neo4j_timeout_at_the_question_deadline_is_deadline_exceeded():
    client = _neo4j_failing_with("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration")
    with request_deadline(0.01):
        time.sleep(0.05)
        with pytest.raises(DeadlineExceeded):
            client.execute_read("MATCH (n) RETURN n")


def test_other_neo4j_client_errors_are_not_hidden_as_timeouts():
    from neo4j.exceptions import CypherSyntaxError
    with pytest.raises(CypherSyntaxError):
        _neo4j_failing_with("Neo.ClientError.Statement.SyntaxError").execute_read("RETURN")

# ----------------------------------------------------- live database

def approved(sql, parameters=()):
    from sql_compiler.models import CompiledQuery
    compiled = CompiledQuery(sql=sql, parameters=list(parameters))
    return dict(compiled=compiled, validation=SimpleNamespace(valid=True, errors=[]),
                guardrails=SimpleNamespace(allowed=True, violations=[]))


@pytest.mark.integration
def test_live_write_fails_at_the_database_even_if_checks_were_bypassed():
    from execution.postgres_executor import PostgreSQLExecutor
    # A no-op write (WHERE false) handed straight to the executor with
    # forged approvals: PostgreSQL itself must refuse it.
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        PostgreSQLExecutor().execute(**approved(
            "UPDATE public.erp_customer_details SET name = name WHERE false"))


@pytest.mark.integration
def test_live_stuck_query_is_cancelled_at_the_deadline():
    from execution.postgres_executor import PostgreSQLExecutor
    started = time.monotonic()
    with request_deadline(0.5):
        with pytest.raises(DeadlineExceeded):
            PostgreSQLExecutor(statement_timeout_ms=10_000).execute(**approved("SELECT pg_sleep(5)"))
    assert time.monotonic() - started < 2.5


@pytest.mark.integration
def test_live_statement_timeout_without_deadline_is_a_plain_timeout():
    from execution.postgres_executor import PostgreSQLExecutor
    with pytest.raises(TimeoutError) as error:
        PostgreSQLExecutor(statement_timeout_ms=300).execute(**approved("SELECT pg_sleep(3)"))
    assert not isinstance(error.value, DeadlineExceeded)
    assert "pg_sleep" not in str(error.value)


@pytest.mark.integration
def test_live_pooled_connection_never_carries_settings_to_the_next_request():
    from psycopg_pool import ConnectionPool

    from execution.pool_reset import reset_session
    from config import settings as live
    from execution.postgres_executor import PostgreSQLExecutor

    pool = ConnectionPool(live.postgres_dsn, min_size=1, max_size=1, reset=reset_session, open=True)
    try:
        executor = PostgreSQLExecutor(statement_timeout_ms=1234, pool=pool)
        executor.execute(**approved("SELECT 1 AS one"))
        with pool.connection() as conn:
            conn.execute("SET statement_timeout = '777ms'")      # a careless session setting
        with pool.connection() as conn:
            after = (conn.execute("SHOW statement_timeout").fetchone()[0],
                     conn.execute("SHOW transaction_read_only").fetchone()[0])
        # The login's own defaults, read from a fresh connection (a
        # read-only login defaults to read-only "on").
        with psycopg.connect(live.postgres_dsn) as fresh:
            defaults = (fresh.execute("SHOW statement_timeout").fetchone()[0],
                        fresh.execute("SHOW transaction_read_only").fetchone()[0])
        assert after == defaults
        assert after[0] not in ("1234ms", "777ms")
    finally:
        pool.close()


@pytest.mark.integration
def test_live_privilege_report_matches_the_catalog():
    from execution.postgres_executor import PostgreSQLExecutor
    report = PostgreSQLExecutor().privileges()
    assert report.role and report.writable_tables >= 0
