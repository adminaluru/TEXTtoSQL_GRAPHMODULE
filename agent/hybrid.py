"""Answer one chat question from the right sources (Step 11, decision D-011).

    question -> route plan (rules; AI only when unsure; both when unclear)
             -> database only   : the existing SQL agent, unchanged
             -> documents only  : quoted passages with citations
             -> both            : both run in parallel within one deadline

Rules
- Simple numeric questions never touch documents (the router guarantees it).
- No fallback that changes the question: if the database part cannot
  answer, documents are not searched instead (unless the plan already
  included them).
- A failed part is said plainly; a denied caller is denied for the whole
  question.
- Passages are quoted exactly. With a writer (Step 12a, D-012), document
  answers are also written from them: each sentence cites its passages and
  is checked; when nothing passes, the quoted passages are the answer.
- Questions needing both sources (Step 12b) get one answer when the database
  part is a figure: the AI sees the figure only as a placeholder, and the
  exact value is put back after the checks. Otherwise, or when the combined
  answer fails the checks, the two parts are shown side by side.
"""
import contextvars
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from answering.models import AnswerResponse, SourcePassage
from grounded_answers import Fact, GroundedAnswerWriter, Passage
from hybrid_contracts import AccessContext, ClarificationResponse, RetrievalRequest, Route
from observability.deadline import remaining_seconds, request_deadline
from observability.perf import request_id_var
from question_router import QuestionRouter, RouteVocabulary

logger = logging.getLogger("uvicorn.error")

VOCABULARY_FILE = Path(__file__).with_name("route_vocabulary.json")

DOCUMENTS_FOUND = "Here is what your documents say. Each passage is quoted exactly, with where it comes from."
DOCUMENTS_NOT_ANSWERED = ("Your documents do not directly answer this. These are the closest passages, "
                          "quoted exactly.")
SENTENCES_LEFT_OUT = "{} sentence(s) were left out because they could not be matched to the documents."
DATABASE_PART_FAILED = "The business-data part of this question could not be answered right now."
DATABASE_PART_UNANSWERED = "The business data could not answer this part: {}"
DOCUMENTS_PART_FAILED = "The documents could not be searched right now."

ROUTE_LABEL = {(Route.SQL,): "database", (Route.DOCUMENT,): "documents", (Route.SQL, Route.DOCUMENT): "both"}


def load_route_vocabulary(path: Path = VOCABULARY_FILE) -> RouteVocabulary:
    data = json.loads(path.read_text(encoding="utf-8"))
    return RouteVocabulary(record_words=frozenset(w.casefold() for w in data["record_words"]))


class HybridAssistant:
    """Same interface as the research agent: ask() and resume()."""

    def __init__(self, agent, router: QuestionRouter, access: AccessContext,
                 document_retriever=None, has_documents: Callable[[str], bool] | None = None,
                 request_timeout_seconds: float = 60.0, passage_limit: int = 5,
                 answer_writer: GroundedAnswerWriter | None = None):
        self._agent = agent
        self._writer = answer_writer
        self._router = router
        self._access = access
        self._documents = document_retriever
        self._has_documents = has_documents
        self._timeout = request_timeout_seconds
        self._passage_limit = passage_limit

    def resume(self, response: ClarificationResponse, caller) -> AnswerResponse:
        # Choices come only from the database part.
        answer = self._agent.resume(response, caller=caller)
        answer.route = "database"
        return answer

    def ask(self, question: str, caller=None) -> AnswerResponse:
        started = time.perf_counter()
        with request_deadline(self._timeout):
            available = {Route.SQL}
            if self._documents is not None and self._has_documents is not None:
                try:
                    if self._has_documents(self._access.tenant_id):
                        available.add(Route.DOCUMENT)
                except Exception:
                    # A document store problem must not stop database questions.
                    logger.warning("FLOWONE_ROUTE document availability check failed", exc_info=True)
            plan = self._router.plan(question, available)
            timings: dict[str, float] = {}

            if plan.routes == (Route.SQL,):
                answer = self._timed(timings, "database", lambda: self._agent.ask(question, caller=caller))
            elif plan.routes == (Route.DOCUMENT,):
                found = self._timed(timings, "documents", lambda: self._search(question))
                answer = self._timed(timings, "writing", lambda: self._documents_answer(found, question))
            elif plan.routes == (Route.SQL, Route.DOCUMENT):
                answer = self._both(question, caller, timings, plan.database_question or question)
            else:
                answer = AnswerResponse(answer=plan.notes[0], result_type="documents", row_count=0)

        answer.route = ROUTE_LABEL.get(plan.routes, "documents")
        answer.warnings.extend(n for n in plan.notes if n != answer.answer)
        trace = {"route": answer.route, "method": plan.method, "classifier_calls": plan.classifier_calls,
                 "signals": list(plan.signals), "timings_ms": timings,
                 "total_ms": round((time.perf_counter() - started) * 1000, 1)}
        answer._route_trace = trace
        logger.info("FLOWONE_ROUTE request_id=%s route=%s method=%s classifier_calls=%s timings_ms=%s total_ms=%s",
                    request_id_var.get(), trace["route"], plan.method, plan.classifier_calls, timings,
                    trace["total_ms"])
        return answer

    # ======================================================
    # Steps
    # ======================================================

    def _search(self, question: str):
        left = remaining_seconds()
        # No time left means the retriever reports the deadline as passed.
        seconds = self._timeout if left is None else max(0.0, left)
        return self._documents.retrieve(RetrievalRequest(
            request_id=request_id_var.get() or "hybrid",
            question=question,
            access=self._access,
            route=Route.DOCUMENT,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=seconds),
            max_evidence_items=self._passage_limit,
        ))

    def _both(self, question: str, caller, timings, database_question: str) -> AnswerResponse:
        # Independent steps: run together, each in a copy of this request's
        # context so the shared deadline applies to both. The database gets
        # only its half of a clear two-part question; documents and the
        # written answer use the whole question.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="flowone-hybrid") as pool:
            database = pool.submit(contextvars.copy_context().run, self._timed, timings, "database",
                                   lambda: self._agent.ask(database_question, caller=caller))
            documents = pool.submit(contextvars.copy_context().run, self._timed, timings, "documents",
                                    lambda: self._search(question))
            found = documents.result()
            try:
                answer = database.result()
            except PermissionError:
                raise
            except Exception:
                if found.status != "complete":
                    raise
                logger.warning("FLOWONE_ROUTE database part failed; documents answered", exc_info=True)
                result = self._documents_answer(found, question)
                result.warnings.insert(0, DATABASE_PART_FAILED)
                return result

        passages = _passages(found)
        if found.status in ("denied",):
            raise PermissionError(found.warnings[0])
        # The database could not interpret the question, but documents can:
        # lead with the passages and say what the database said.
        if answer.result_type == "clarification" and not _offers_choices(answer) and passages:
            result = self._documents_answer(found, question)
            result.warnings.insert(0, DATABASE_PART_UNANSWERED.format(answer.answer))
            return result

        answer.passages = passages
        if passages and self._writer is not None:
            self._combine(answer, question)
        if not passages:
            answer.warnings.append(DOCUMENTS_PART_FAILED if found.status in ("unavailable", "failed")
                                   else f"Documents: {found.warnings[0]}" if found.warnings else "Documents: nothing found.")
        return answer

    def _documents_answer(self, found, question: str) -> AnswerResponse:
        if found.status == "denied":
            raise PermissionError(found.warnings[0])
        passages = _passages(found)
        if passages:
            answer = AnswerResponse(answer=DOCUMENTS_FOUND, result_type="documents",
                                    row_count=len(passages), passages=passages, answer_basis="quoted")
            if self._writer is not None:
                self._write(answer, question)
            return answer
        message = found.warnings[0] if found.warnings else DOCUMENTS_PART_FAILED
        return AnswerResponse(answer=message, result_type="documents", row_count=0)

    def _write(self, answer: AnswerResponse, question: str) -> None:
        written = self._writer.write(question, [Passage(text=p.text, source=p.citation) for p in answer.passages])
        logger.info("FLOWONE_GROUNDED request_id=%s status=%s kept=%s dropped=%s reasons=%s",
                    request_id_var.get(), written.status, len(written.sentences), len(written.dropped),
                    list(written.dropped))
        if written.status == "written":
            answer.answer, answer.answer_basis = written.text, "written"
            if written.dropped:
                answer.warnings.append(SENTENCES_LEFT_OUT.format(len(written.dropped)))
        elif written.status == "not_answered":
            answer.answer = DOCUMENTS_NOT_ANSWERED

    def _combine(self, answer: AnswerResponse, question: str) -> None:
        facts = _facts(answer)
        if not facts:
            # Tables, clarifications and answers without a figure keep the
            # side-by-side layout.
            answer.answer_basis = "quoted"
            return
        written = self._writer.write(question, [Passage(text=p.text, source=p.citation) for p in answer.passages],
                                     facts)
        logger.info("FLOWONE_GROUNDED request_id=%s combined=true status=%s kept=%s dropped=%s reasons=%s",
                    request_id_var.get(), written.status, len(written.sentences), len(written.dropped),
                    list(written.dropped))
        if written.status == "written":
            answer.answer, answer.answer_basis = written.text, "written"
            if written.dropped:
                answer.warnings.append(SENTENCES_LEFT_OUT.format(len(written.dropped)))
        else:
            answer.answer_basis = "quoted"

    @staticmethod
    def _timed(timings: dict, name: str, action):
        started = time.perf_counter()
        try:
            return action()
        finally:
            timings[name] = round((time.perf_counter() - started) * 1000, 1)


def _passages(found) -> list[SourcePassage]:
    if found.status != "complete":
        return []
    return [SourcePassage(document_id=e.sources[0].source_id, version=int(e.sources[0].version),
                          citation=e.sources[0].location, text=e.content) for e in found.evidence]


def _facts(answer: AnswerResponse) -> list[Fact]:
    """
    The database answer's figures as placeholders {DB1}, {DB2}... in its own
    sentence. Empty when the answer is not a figure, or a figure cannot be
    found exactly once-or-more as a whole token (then nothing is combined).
    """
    if answer.result_type not in ("scalar", "tabular") or not answer._figures:
        return []
    statement, values = answer.answer, []
    for number, figure in enumerate(dict.fromkeys(answer._figures), start=1):
        # Whole token only: "24" must not match inside "2024" or "24.5".
        pattern = re.compile(rf"(?<![\w.,]){re.escape(figure)}(?![\w,]|\.\d)")
        if not pattern.search(statement):
            return []
        statement = pattern.sub(lambda m, n=number: f"{{DB{n}}}", statement)
        values.append((f"DB{number}", figure))
    return [Fact(id=fact_id, statement=statement, value=value) for fact_id, value in values]


def _offers_choices(answer: AnswerResponse) -> bool:
    return isinstance(answer.data, dict) and "clarification_id" in answer.data
