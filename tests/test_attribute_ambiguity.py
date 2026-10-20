"""A bare attribute that matches several columns asks the user which one is meant.

Before, "status" on invoices (lifecycle, transaction and approval status)
ended, after repair attempts, as the generic "I couldn't match your question".
Repair still runs (a rephrased interpretation may name one attribute); if it
does not resolve the tie, the user sees the business labels to choose from.
Physical table and column names are never shown.
"""
from types import SimpleNamespace

import pytest

from agent.repair import RepairState, unresolved_message
from planning.column_resolver import ColumnResolver
from planning.errors import AmbiguousAttributeError


def column(name, description, category="dimension", ordinal=1):
    return SimpleNamespace(name=name, description=description, category=category,
                           data_type="VARCHAR(10)", key=f"public.erp_invoice_details.{name}", ordinal=ordinal,
                           is_primary_key=False, semantic_role=None, projection_role=None)


STATUS = [
    # Ontology positions: options are offered in table order.
    column("inv_status", "Invoice lifecycle status. Sample: OPEN, CLOSE.", ordinal=29),
    column("transaction_status", "Transaction status. Sample: PAID, OVERDUE.", ordinal=30),
    column("approval_status", "Approval status. Sample: NA.", ordinal=56),
]
QUESTION = ('"status" could mean invoice lifecycle status, transaction status or approval status. '
            "Which one do you mean?")


@pytest.fixture
def resolver():
    return ColumnResolver.__new__(ColumnResolver)


def test_tied_attribute_raises_with_a_business_question(resolver):
    with pytest.raises(AmbiguousAttributeError) as caught:
        resolver._resolve_attribute("Invoice", "status", STATUS, prefer_measure=False)
    assert caught.value.user_message == QUESTION
    # Internal detail (for logs and the repair prompt) keeps the candidates.
    assert "inv_status" in str(caught.value)


def test_ambiguity_is_still_a_value_error_so_repair_runs():
    assert issubclass(AmbiguousAttributeError, ValueError)


@pytest.mark.parametrize("columns", [
    [column("a_status", "Status. Sample: X."), column("b_status", None)],            # missing label
    [column("a_status", "Status. Sample: X."), column("b_status", "status.")],       # same label twice
])
def test_without_distinct_labels_the_question_stays_generic(columns):
    question = ColumnResolver._ambiguity_question("status", columns)
    assert question == ('"status" could mean more than one thing in the data. '
                        "Please say more precisely which one you mean.")
    assert "a_status" not in question and "b_status" not in question


def test_unresolved_repair_shows_the_latest_ambiguity_question():
    assert unresolved_message(RepairState(clarification=QUESTION)) == QUESTION
    assert unresolved_message(RepairState()).startswith("I couldn't match your question")


# ----------------------------------------------------------
# Live, read-only: the real ontology's three invoice status columns
# ----------------------------------------------------------

def live_status_answer(value):
    from agent.factory import build_research_agent
    from ai.providers.mock_chat import MockChatModel
    from database.neo4j import Neo4jClient
    from execution.postgres_executor import PostgreSQLExecutor

    response = {"intent": "count", "concepts": ["Invoice"], "result_concept": "Invoice",
                "measure_concept": "Invoice", "measure_attribute": None, "aggregation": "COUNT",
                "group_by_concepts": [],
                "entities": [{"concept": "Invoice", "attribute": "status", "value": value}]}
    neo4j = Neo4jClient()
    try:
        agent = build_research_agent(
            chat_model=MockChatModel(structured_response=response), neo4j_client=neo4j,
            postgres_executor=PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100))
        return agent.ask(f"How many invoices have status {value}?")
    finally:
        neo4j.close()


@pytest.mark.integration
def test_live_bare_status_is_decided_by_the_value_only_one_status_holds():
    # OPEN is stored only in the invoice lifecycle status.
    answer = live_status_answer("OPEN")
    assert answer.result_type == "scalar"


@pytest.mark.integration
def test_live_bare_status_with_a_value_no_status_holds_asks_which_status():
    assert live_status_answer("NOT-A-STATUS").answer == QUESTION

def test_low_scoring_tie_offers_no_choice(resolver):
    # A nonsense phrase scores equally low on every column: not a real choice.
    with pytest.raises(AmbiguousAttributeError) as caught:
        resolver._resolve_attribute("Invoice", "zzqx flarb quotient", STATUS, prefer_measure=False)
    assert caught.value.user_message is None
    assert unresolved_message(RepairState(clarification=caught.value.user_message)).startswith(
        "I couldn't match your question")


def test_too_many_candidates_offer_no_choice():
    columns = [column(f"s{i}", f"Status {i}. Sample: X.") for i in range(5)]
    assert ColumnResolver._ambiguity_question("status", columns) is None


def test_notes_in_brackets_are_left_out_of_labels():
    columns = [column("a", "Amount paid in the receipt (DOUBLE in DDL). Sample: 1."),
               column("b", "Bank charges on the payment (NULL in sample).")]
    assert ColumnResolver._ambiguity_question("amount", columns) == (
        '"amount" could mean amount paid in the receipt or bank charges on the payment. '
        "Which one do you mean?")


# A tie between equally named columns is decided by the condition's value when
# exactly one tied column holds it (looked up inside the caller's scope).

def test_value_held_by_one_tied_column_decides():
    resolver = ColumnResolver.__new__(ColumnResolver)
    resolver._value_matcher = lambda table, column, value: column == "inv_status"
    assert resolver._choose_by_value("public.erp_invoice_details", "open", tuple(STATUS)).name == "inv_status"


@pytest.mark.parametrize("matcher", [
    lambda table, column, value: True,      # held by several: never guess
    lambda table, column, value: False,     # held by none
    lambda table, column, value: None,      # cannot tell (large domain)
])
def test_value_that_does_not_single_out_a_column_leaves_the_tie(matcher):
    resolver = ColumnResolver.__new__(ColumnResolver)
    resolver._value_matcher = matcher
    assert resolver._choose_by_value("public.erp_invoice_details", "open", tuple(STATUS)) is None


def test_without_a_value_matcher_ties_are_clarified():
    resolver = ColumnResolver.__new__(ColumnResolver)
    resolver._value_matcher = None
    assert resolver._choose_by_value("t", "open", tuple(STATUS)) is None
