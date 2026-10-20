"""Step 12c (D-012): saved conversations, private to one user in one company."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from adapters.conversation_store import TITLE_CHARS, SqliteConversationStore
from answering.models import AnswerResponse
from api.application import create_app
from trusted_identity import IdentityVerifier
from tests.test_api_identity import SETTINGS, fake_agent, headers, token


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def store(tmp_path):
    s = SqliteConversationStore(tmp_path / "conversations.sqlite3", clock=Clock())
    yield s
    s.close()


ANSWER = {"answer": "24 invoices are open.", "result_type": "scalar", "row_count": 1, "data": None, "warnings": []}


# ================================================================ store

def test_messages_are_saved_in_order_and_titled_by_the_first_question(store):
    store.add_question("acme", "user:alice", "c1", "How many open invoices are there?")
    store.add_answer("acme", "user:alice", "c1", ANSWER)
    store.add_question("acme", "user:alice", "c1", "And closed ones?")
    messages = store.messages("acme", "user:alice", "c1")
    assert [m.kind for m in messages] == ["question", "answer", "question"]
    assert messages[1].content == ANSWER
    summary = store.summary("acme", "user:alice", "c1")
    assert summary.title == "How many open invoices are there?" and summary.message_count == 3


def test_list_is_newest_first(store):
    store.add_question("acme", "user:alice", "old", "First")
    store.add_question("acme", "user:alice", "new", "Second")
    store.add_question("acme", "user:alice", "old", "Third")  # activity moves it to the top
    assert [c.conversation_id for c in store.recent("acme", "user:alice")] == ["old", "new"]


@pytest.mark.parametrize("tenant,subject", [("acme", "user:bob"), ("nova", "user:alice"), ("acme", "service:alice")])
def test_another_user_or_company_never_sees_or_changes_it(store, tenant, subject):
    store.add_question("acme", "user:alice", "c1", "Secret question")
    assert store.recent(tenant, subject) == []
    assert store.summary(tenant, subject, "c1") is None and store.messages(tenant, subject, "c1") is None
    assert store.delete(tenant, subject, "c1") is False
    # The same id for someone else is a separate conversation.
    store.add_question(tenant, subject, "c1", "Mine")
    assert [m.content for m in store.messages("acme", "user:alice", "c1")] == ["Secret question"]


def test_delete_removes_the_conversation_and_its_messages(store):
    store.add_question("acme", "user:alice", "c1", "Q")
    store.add_answer("acme", "user:alice", "c1", ANSWER)
    assert store.delete("acme", "user:alice", "c1") is True
    assert store.messages("acme", "user:alice", "c1") is None and store.recent("acme", "user:alice") == []
    assert store.delete("acme", "user:alice", "c1") is False


def test_choice_is_saved_with_the_label_of_the_offered_option(store):
    clarification = {**ANSWER, "result_type": "clarification", "data": {
        "clarification_id": "x", "options": [{"option_id": "o1", "label": "Blue Prism LE-1001 — Acme Primary"}]}}
    store.add_question("acme", "user:alice", "c1", "Blue Prism claims?")
    store.add_answer("acme", "user:alice", "c1", clarification)
    store.add_choice("acme", "user:alice", "c1", "o1")
    store.add_choice("acme", "user:alice", "c1", "unknown")
    choices = [m.content for m in store.messages("acme", "user:alice", "c1") if m.kind == "choice"]
    assert choices == [{"option_id": "o1", "label": "Blue Prism LE-1001 — Acme Primary"},
                       {"option_id": "unknown", "label": None}]


def test_long_titles_are_shortened(store):
    store.add_question("acme", "user:alice", "c1", "word " * 100)
    title = store.summary("acme", "user:alice", "c1").title
    assert len(title) == TITLE_CHARS and title.endswith("…")


def test_conversations_survive_a_restart(tmp_path):
    path = tmp_path / "conversations.sqlite3"
    first = SqliteConversationStore(path)
    first.add_question("acme", "user:alice", "c1", "Q")
    first.close()
    second = SqliteConversationStore(path)
    assert [c.conversation_id for c in second.recent("acme", "user:alice")] == ["c1"]
    second.close()


# ================================================================ API

def client(store, agent=None):
    app = create_app(Mock(), identity=IdentityVerifier(SETTINGS), agent_for=lambda access: agent or fake_agent(),
                     rate_limit_requests_per_minute=1000, conversations=store)
    return TestClient(app)


def ask(c, bearer, conversation_id="c1", question="How many open invoices?"):
    body = {"question": question} | ({"conversation_id": conversation_id} if conversation_id else {})
    return c.post("/ask", json=body, headers=headers(bearer))


def test_ask_saves_the_question_and_the_answer_exactly_as_returned(store):
    with client(store) as c:
        response = ask(c, token())
        detail = c.get("/conversations/c1", headers=headers(token())).json()
    assert response.status_code == 200
    assert [m["kind"] for m in detail["messages"]] == ["question", "answer"]
    assert detail["messages"][0]["content"] == "How many open invoices?"
    assert detail["messages"][1]["content"] == response.json()
    assert detail["title"] == "How many open invoices?"


def test_list_and_delete(store):
    with client(store) as c:
        ask(c, token(), "c1")
        ask(c, token(), "c2", "Second question")
        listed = c.get("/conversations", headers=headers(token())).json()["conversations"]
        assert [x["conversation_id"] for x in listed] == ["c2", "c1"]
        assert c.delete("/conversations/c1", headers=headers(token())).status_code == 204
        assert c.get("/conversations/c1", headers=headers(token())).status_code == 404
        assert c.delete("/conversations/c1", headers=headers(token())).status_code == 404


def test_nothing_is_saved_without_a_conversation_id(store):
    with client(store) as c:
        ask(c, token(), conversation_id=None)
        assert c.get("/conversations", headers=headers(token())).json()["conversations"] == []


def test_failed_questions_are_not_saved(store):
    with client(store, fake_agent(error=ValueError("bad"))) as c:
        assert ask(c, token()).status_code == 400
        assert c.get("/conversations", headers=headers(token())).json()["conversations"] == []


def test_another_user_gets_not_found(store):
    with client(store) as c:
        ask(c, token(subject="alice"))
        bob = token(subject="bob")
        assert c.get("/conversations", headers=headers(bob)).json()["conversations"] == []
        assert c.get("/conversations/c1", headers=headers(bob)).status_code == 404
        assert c.delete("/conversations/c1", headers=headers(bob)).status_code == 404
        assert c.get("/conversations/c1", headers=headers(token(subject="alice"))).status_code == 200


def test_switching_company_shows_only_that_company(store):
    both = token(tenants=("tenant-acme", "tenant-nova"))
    with client(store) as c:
        c.post("/ask", json={"question": "Acme question", "conversation_id": "c1"},
               headers=headers(both, tenant="tenant-acme"))
        nova = c.get("/conversations", headers=headers(both, tenant="tenant-nova")).json()["conversations"]
        acme = c.get("/conversations", headers=headers(both, tenant="tenant-acme")).json()["conversations"]
        assert nova == [] and [x["conversation_id"] for x in acme] == ["c1"]
        assert c.get("/conversations/c1", headers=headers(both, tenant="tenant-nova")).status_code == 404


def test_clarify_saves_the_choice_and_the_answer(store):
    clarification = AnswerResponse(answer="Which customer?", result_type="clarification", row_count=0, data={
        "clarification_id": "x", "options": [{"option_id": "o1", "label": "Blue Prism LE-1001"}]})
    agent = fake_agent(result=clarification)
    with client(store, agent) as c:
        ask(c, token(), question="Blue Prism claims?")
        c.post("/clarify", json={"conversation_id": "c1", "clarification_id": "x", "option_id": "o1"},
               headers=headers(token()))
        messages = c.get("/conversations/c1", headers=headers(token())).json()["messages"]
    assert [m["kind"] for m in messages] == ["question", "answer", "choice", "answer"]
    assert messages[2]["content"] == {"option_id": "o1", "label": "Blue Prism LE-1001"}


def test_routes_require_a_token(store):
    with client(store) as c:
        assert c.get("/conversations").status_code == 401
        assert c.get("/conversations/c1").status_code == 401
        assert c.delete("/conversations/c1").status_code == 401


def test_a_storage_failure_never_changes_the_answer(store):
    broken = Mock(wraps=store)
    broken.add_question.side_effect = OSError("disk full")
    with client(broken) as c:
        response = ask(c, token())
    assert response.status_code == 200 and response.json()["answer"] == "ok"


def test_saved_conversations_need_per_user_identity(store):
    with pytest.raises(ValueError, match="AUTH_MODE=jwt"):
        create_app(Mock(), conversations=store)
