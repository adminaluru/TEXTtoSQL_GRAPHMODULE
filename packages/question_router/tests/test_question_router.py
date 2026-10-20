"""Step 11: rules first, classifier only when unsure, both when still unclear (D-011)."""
import pytest
from hybrid_contracts import Route

from question_router import (DOCUMENTS_ONLY_NONE, NO_DOCUMENTS, QuestionRouter, RouteVocabulary, classify,
                             database_part)

VOCAB = RouteVocabulary(record_words=frozenset({"invoice", "payment", "claim", "customer", "promise"}))
ALL = {Route.SQL, Route.DOCUMENT}
SQL, DOC = Route.SQL, Route.DOCUMENT


class Classifier:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def __call__(self, question):
        self.calls.append(question)
        if self.error:
            raise self.error
        return self.answer


@pytest.mark.parametrize("question,routes", [
    ("What is the total invoice amount?", {SQL}),
    ("How many open invoices are there?", {SQL}),
    ("What is the total payment amount in April 2026?", {SQL}),
    ("List invoices for Blue Prism", {SQL}),
    ("Which customers have claims?", {SQL}),
    ("What is the outstanding balance per customer?", {SQL}),
    ("What does our contract say about late payment fees?", {DOC}),
    ("What are the payment terms?", {DOC}),
    ("According to the agreement, when is payment due?", {DOC}),
    ("Which clause covers disputes?", {DOC}),
    ("What does the policy say about late fees, and how many invoices are overdue?", {SQL, DOC}),
    ("How many claims are open and what does the dispute procedure require?", {SQL, DOC}),
    ("Why was invoice INV-1001 adjusted?", {SQL, DOC}),
    ("What is the reason for the claim on Blue Prism's invoice?", {SQL, DOC}),
    ("Why does the policy require approval?", {DOC}),
])
def test_clear_questions_are_routed_by_rules_alone(question, routes):
    verdict = classify(question, VOCAB)
    assert verdict.clear and verdict.routes == routes


@pytest.mark.parametrize("question", [
    "What is the late payment fee?",
    "When should we follow up with Blue Prism?",
    "Tell me about Blue Prism",
    "Why is that?",
])
def test_unclear_wording_is_not_decided_by_rules(question):
    assert not classify(question, VOCAB).clear


def test_simple_numeric_questions_never_call_the_classifier():
    classifier = Classifier(frozenset({DOC}))
    plan = QuestionRouter(VOCAB, classifier).plan("How many open invoices are there?", ALL)
    assert plan.routes == (SQL,) and plan.method == "rules" and plan.classifier_calls == 0
    assert classifier.calls == []


def test_unsure_questions_use_one_classifier_call():
    classifier = Classifier(frozenset({DOC}))
    plan = QuestionRouter(VOCAB, classifier).plan("What is the late payment fee?", ALL)
    assert plan.routes == (DOC,) and plan.method == "classifier" and plan.classifier_calls == 1


@pytest.mark.parametrize("classifier", [None, Classifier(None), Classifier(frozenset()),
                                        Classifier(error=TimeoutError()),
                                        Classifier(frozenset({Route.GRAPH}))],
                         ids=["none", "unclear", "empty", "error", "graph-only"])
def test_still_unclear_asks_both_sources(classifier):
    plan = QuestionRouter(VOCAB, classifier).plan("Tell me about Blue Prism", ALL)
    assert plan.routes == (SQL, DOC) and plan.method == "unclear_both"


def test_documents_are_skipped_with_a_note_when_the_workspace_has_none():
    router = QuestionRouter(VOCAB)
    both = router.plan("What does the policy say about late fees, and how many invoices are overdue?", {SQL})
    assert both.routes == (SQL,) and both.notes == (NO_DOCUMENTS,)
    only_docs = router.plan("What does our contract say about late fees?", {SQL})
    assert only_docs.routes == () and only_docs.notes == (DOCUMENTS_ONLY_NONE,)


def test_the_graph_route_is_never_planned_until_it_is_available():
    plan = QuestionRouter(VOCAB, Classifier(frozenset({Route.GRAPH, SQL}))).plan("Tell me about Blue Prism", ALL)
    assert Route.GRAPH not in plan.routes


def test_routing_reports_its_signals():
    verdict = classify("How many invoices are overdue?", VOCAB)
    assert "record:invoices" in verdict.signals and "measure:how many" in verdict.signals


def test_record_words_come_from_the_application():
    other = RouteVocabulary(record_words=frozenset({"shipment"}))
    assert classify("How many shipments left today?", other).routes == {SQL}
    assert not classify("How many shipments left today?", VOCAB).clear


# Step 12b: the database is asked only its half of a clear two-part question.

@pytest.mark.parametrize("question,part", [
    ("How many invoices are overdue, and what does the policy say about late fees?", "How many invoices are overdue?"),
    ("What does the policy say about late fees, and how many invoices are overdue?", "How many invoices are overdue?"),
    ("What is the total claim amount; what does the dispute procedure require?", "What is the total claim amount?"),
])
def test_two_part_question_gives_the_database_its_half(question, part):
    assert database_part(question, VOCAB) == part


@pytest.mark.parametrize("question", [
    "How many invoices and payments are there?",                                   # one part
    "What does the policy say about late fees and what does the contract say?",    # documents only
    "How many invoices are overdue and tell me about Blue Prism?",                 # a part the rules cannot place
])
def test_no_split_unless_every_part_is_clearly_one_kind(question):
    assert database_part(question, VOCAB) is None


def test_plan_carries_the_database_half_only_for_both_routes():
    router = QuestionRouter(VOCAB)
    both = router.plan("How many invoices are overdue, and what does the policy say about late fees?", ALL)
    assert both.routes == (SQL, DOC) and both.database_question == "How many invoices are overdue?"
    assert router.plan("How many invoices are overdue?", ALL).database_question is None
    # Documents unavailable: database only, whole question.
    assert router.plan("How many invoices are overdue, and what does the policy say about late fees?",
                       {SQL}).database_question is None
