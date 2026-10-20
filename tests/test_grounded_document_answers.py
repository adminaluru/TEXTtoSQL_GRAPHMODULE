"""Step 12a (D-012): document answers written from the passages, each sentence cited and checked."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from grounded_answers import Draft, DraftSentence, GroundedAnswerWriter

from agent.hybrid import DOCUMENTS_FOUND, DOCUMENTS_NOT_ANSWERED, HybridAssistant, load_route_vocabulary
from answering.grounded_drafter import chat_drafter
from api.identity_setup import require_safe_grounded_answers
from question_router import QuestionRouter
from tests.test_hybrid_assistant import ACME, DOC_QUESTION, SQL_QUESTION, FakeDocuments, scalar

FEE = "Late payments incur a fee of 2% per month."
APPROVAL = "Write-offs above 10,000 USD must be approved by the Credit Manager."


class FakeDraft:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    def __call__(self, instructions, payload):
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply


def written(*sentences, answerable=True):
    return Draft(answerable=answerable, sentences=[DraftSentence(text=t, sources=list(s)) for t, *s in sentences])


def hybrid(draft, texts=(FEE, APPROVAL)):
    agent = Mock(ask=Mock(return_value=scalar()))
    return HybridAssistant(agent, QuestionRouter(load_route_vocabulary()), ACME,
                           document_retriever=FakeDocuments(texts), has_documents=lambda tenant: True,
                           answer_writer=GroundedAnswerWriter(draft))


def test_document_answer_is_written_with_passage_numbers_and_keeps_the_passages():
    answer = hybrid(FakeDraft(written(("Late payments incur a fee of 2% per month.", 1)))).ask(DOC_QUESTION)
    assert answer.answer == "Late payments incur a fee of 2% per month. [1]"
    assert answer.answer_basis == "written"
    assert [p.text for p in answer.passages] == [FEE, APPROVAL]
    assert answer.route == "documents" and answer._route_trace["timings_ms"]["writing"] >= 0


def test_sentences_failing_the_checks_are_left_out_and_reported():
    draft = FakeDraft(written(("Late payments incur a fee of 2% per month.", 1),
                              ("Write-offs above 50,000 USD need the board.", 2)))
    answer = hybrid(draft).ask(DOC_QUESTION)
    assert answer.answer == "Late payments incur a fee of 2% per month. [1]"
    assert "1 sentence(s) were left out" in answer.warnings[0]


@pytest.mark.parametrize("draft,expected", [
    (FakeDraft(written(answerable=False)), DOCUMENTS_NOT_ANSWERED),
    (FakeDraft(written(("All fees are waived.",))), DOCUMENTS_FOUND),          # nothing passed
    (FakeDraft(error=TimeoutError()), DOCUMENTS_FOUND),                      # model failed
])
def test_without_a_checked_sentence_the_passages_are_the_answer(draft, expected):
    answer = hybrid(draft).ask(DOC_QUESTION)
    assert answer.answer == expected and answer.answer_basis == "quoted"
    assert [p.text for p in answer.passages] == [FEE, APPROVAL]


def test_no_passages_means_no_model_call():
    draft = FakeDraft(written(("x", 1)))
    answer = hybrid(draft, texts=()).ask(DOC_QUESTION)
    assert draft.calls == 0 and not answer.passages


def test_database_questions_never_reach_the_writer():
    draft = FakeDraft(written(("x", 1)))
    answer = hybrid(draft).ask(SQL_QUESTION)
    assert draft.calls == 0 and answer.answer_basis is None and "answer_basis" not in answer.model_dump(
        exclude_none=True)


def test_without_a_writer_documents_are_quoted_as_before():
    agent = Mock(ask=Mock(return_value=scalar()))
    answer = HybridAssistant(agent, QuestionRouter(load_route_vocabulary()), ACME, document_retriever=FakeDocuments(),
                             has_documents=lambda tenant: True).ask(DOC_QUESTION)
    assert answer.answer == DOCUMENTS_FOUND and answer.answer_basis == "quoted"


def test_chat_drafter_sends_fixed_instructions_and_passages_as_data():
    chat = Mock()
    chat.generate_structured.return_value = written(("Late payments incur a fee of 2% per month.", 1))
    draft = chat_drafter(chat)("INSTRUCTIONS", '{"question": "Q"}')
    request = chat.generate_structured.call_args.kwargs["request"]
    assert [m.role for m in request.messages] == ["system", "user"]
    assert request.messages[0].content == "INSTRUCTIONS" and request.messages[1].content == '{"question": "Q"}'
    assert request.temperature == 0.0 and chat.generate_structured.call_args.kwargs["response_schema"] is Draft
    assert draft.sentences[0].sources == [1]


@pytest.mark.parametrize("env,enabled,refused", [
    ("production", True, True), ("prod", True, True), ("production", False, False), ("dev", True, False)])
def test_production_refuses_sending_document_text_to_the_ai(env, enabled, refused):
    settings = SimpleNamespace(app_env=env, grounded_answers_enabled=enabled)
    if refused:
        with pytest.raises(RuntimeError, match="D-012"):
            require_safe_grounded_answers(settings)
    else:
        require_safe_grounded_answers(settings)


# ---------------------------------------------------------------- 12b: combined answers

import json  # noqa: E402

from tests.test_hybrid_assistant import BOTH_QUESTION  # noqa: E402

COUNT_TEXT = "The count of invoice records with invoice status overdue is 24."


def counted(figures=("24",), text=COUNT_TEXT):
    answer = scalar(text=text)
    answer._figures = figures
    return answer


def combined(draft, database_answer):
    agent = Mock(ask=Mock(return_value=database_answer))
    return HybridAssistant(agent, QuestionRouter(load_route_vocabulary()), ACME,
                           document_retriever=FakeDocuments((FEE, APPROVAL)), has_documents=lambda tenant: True,
                           answer_writer=GroundedAnswerWriter(draft))


class RecordingDraft(FakeDraft):
    def __call__(self, instructions, payload):
        self.payload = json.loads(payload)
        return super().__call__(instructions, payload)


def test_both_question_gets_one_answer_with_the_exact_database_figure():
    draft = RecordingDraft(Draft(answerable=True, sentences=[
        DraftSentence(text="{DB1} invoices are overdue.", database_fact_ids=["DB1"]),
        DraftSentence(text="Late payments incur a fee of 2% per month.", sources=[1])]))
    answer = combined(draft, counted()).ask(BOTH_QUESTION)
    assert answer.answer == "24 invoices are overdue. [database] Late payments incur a fee of 2% per month. [1]"
    assert answer.answer_basis == "written" and answer.route == "both"
    assert answer.data == {"alias": "count", "value": 24}, "structured data stays the database's"
    assert draft.payload["database_facts"] == [
        {"id": "DB1", "statement": "The count of invoice records with invoice status overdue is {DB1}."}]
    assert "24" not in json.dumps(draft.payload), "the AI never sees the figure"


def test_combined_answer_failing_the_checks_keeps_the_parts_side_by_side():
    draft = FakeDraft(Draft(answerable=True, sentences=[
        DraftSentence(text="Late payments incur a fee of 2% per month.", sources=[1])]))  # figure left out
    answer = combined(draft, counted()).ask(BOTH_QUESTION)
    assert answer.answer == COUNT_TEXT and answer.answer_basis == "quoted"
    assert [p.text for p in answer.passages] == [FEE, APPROVAL]


@pytest.mark.parametrize("database_answer", [
    counted(figures=()),                                    # no figure recorded (e.g. a table)
    counted(figures=("25",)),                               # figure not found in the text: never guess
])
def test_without_a_usable_figure_nothing_is_combined(database_answer):
    draft = FakeDraft(Draft(answerable=True, sentences=[]))
    answer = combined(draft, database_answer).ask(BOTH_QUESTION)
    assert draft.calls == 0 and answer.answer == COUNT_TEXT and answer.answer_basis == "quoted"


def test_database_clarification_is_never_combined():
    from agent.hybrid import DATABASE_PART_UNANSWERED
    from answering.models import AnswerResponse
    clarification = AnswerResponse(answer="Which customer do you mean?", result_type="clarification", row_count=0)
    draft = FakeDraft(Draft(answerable=True, sentences=[]))
    answer = combined(draft, clarification).ask(BOTH_QUESTION)
    # The documents answer instead (12a), and the user is told what the database said.
    assert answer.answer == DOCUMENTS_FOUND and answer.answer_basis == "quoted"
    assert answer.warnings[0] == DATABASE_PART_UNANSWERED.format("Which customer do you mean?")


def test_database_is_asked_only_its_half_of_a_two_part_question():
    agent = Mock(ask=Mock(return_value=counted()))
    hybrid = HybridAssistant(agent, QuestionRouter(load_route_vocabulary()), ACME,
                             document_retriever=FakeDocuments((FEE,)), has_documents=lambda tenant: True)
    hybrid.ask("How many invoices are overdue, and what does the policy say about late payment fees?")
    assert agent.ask.call_args.args[0] == "How many invoices are overdue?"
