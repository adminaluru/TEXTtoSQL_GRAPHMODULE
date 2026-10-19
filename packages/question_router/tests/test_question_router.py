"""Step 11: rules first, classifier only when unsure, both when still unclear (D-011)."""
import pytest
from hybrid_contracts import Route

from question_router import (DOCUMENTS_ONLY_NONE, NO_DOCUMENTS, QuestionRouter, RouteVocabulary, classify)

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
])
def test_clear_questions_are_routed_by_rules_alone(question, routes):
    verdict = classify(question, VOCAB)
    assert verdict.clear and verdict.routes == routes


@pytest.mark.parametrize("question", [
    "What is the late payment fee?",
    "When should we follow up with Blue Prism?",
    "Tell me about Blue Prism",
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
