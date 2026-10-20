"""Evaluation of written document answers (Step 12.0, decision D-012).

Builds the synthetic documents of docs/benchmarks/document_answers.v1.json in
a temporary store, searches them for each question, and checks the result:

    python -m tools.run_answer_eval --dry-run   search only: is the right passage found? No AI, no cost
    python -m tools.run_answer_eval --live      also writes each answer with the AI model (costs tokens)

A question passes (live) when:
    answerable      a written answer contains every must_include, no must_exclude,
                    and cites a passage from the expected section
    not answerable  no written answer is shown (the passages are quoted instead)
    combined        (Step 12b; real database for tenant-acme) one written answer that
                    contains every database figure exactly and every must_include

The report is written to docs/benchmarks/results/.
"""
import argparse
import json
import logging
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hybrid_contracts import AccessContext, RetrievalRequest, Route

ROOT = Path(__file__).parents[1]
QUESTIONS = ROOT / "docs" / "benchmarks" / "document_answers.v1.json"
RESULTS = ROOT / "docs" / "benchmarks" / "results"
ACCESS = AccessContext(subject_id="answer-eval", tenant_id="tenant-acme", policy_version="eval")
PASSAGE_LIMIT = 5


def load(path: Path = QUESTIONS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def normalise(text: str) -> str:
    # Numbers compare without thousands separators: "10,000" == "10000".
    return re.sub(r"(?<=\d),(?=\d{3})", "", text).casefold()


def contains(text: str, phrase: str) -> bool:
    return normalise(phrase) in normalise(text)


class DocumentStore:
    """The evaluation documents in a temporary SQLite store, searchable like the application's."""

    def __init__(self, folder: Path, documents: list[dict]):
        from adapters.document_files import LocalFolderFileStore
        from adapters.document_search import SqliteDocumentDirectory, SqlitePassageSearch
        from adapters.document_sqlite import SqliteDatabase, SqliteDocumentCatalog, SqlitePassageIndex
        from document_ingestion import DocumentIngestionService
        from document_retrieval import DocumentRetriever
        from tests.document_test_files import make_docx, make_pdf

        self._database = SqliteDatabase(folder / "catalog.sqlite3")
        queue: list[str] = []
        service = DocumentIngestionService(SqliteDocumentCatalog(self._database),
                                           LocalFolderFileStore(folder / "files"),
                                           SqlitePassageIndex(self._database), enqueue=queue.append)
        for document in documents:
            if "pages" in document:
                content = make_pdf(document["pages"])
            else:
                def build(doc, document=document):
                    doc.add_heading(document["title"], level=0)
                    for heading, text in document["sections"]:
                        doc.add_heading(heading, level=1)
                        doc.add_paragraph(text)
                content = make_docx(build)
            service.upload(ACCESS, document["file"], content)
        while queue:
            service.process(queue.pop(0))
        self._retriever = DocumentRetriever(SqlitePassageSearch(self._database), SqliteDocumentDirectory(self._database))

    def search(self, question: str):
        """[(text, citation)] in rank order."""
        result = self._retriever.retrieve(RetrievalRequest(
            request_id="answer-eval", question=question, access=ACCESS, route=Route.DOCUMENT,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30), max_evidence_items=PASSAGE_LIMIT))
        return [(e.content, e.sources[0].location) for e in result.evidence] if result.status == "complete" else []

    def close(self):
        self._database.close()


def cites(passages, numbers, section: str | None) -> bool:
    if section is None:
        return True
    return any(contains(passages[n - 1][1], section) or contains(passages[n - 1][0], section) for n in numbers)


def found(passages, section: str | None) -> bool:
    return section is None or any(contains(citation, section) or contains(text, section)
                                  for text, citation in passages)


def evaluate(data: dict, writer=None, usage=None, chat=None) -> list[dict]:
    rows = []
    with tempfile.TemporaryDirectory(prefix="flowone-answer-eval-") as folder:
        store = DocumentStore(Path(folder), data["documents"])
        try:
            for item in data["questions"]:
                passages = store.search(item["question"])
                row = {"id": item["id"], "answerable": item["answerable"], "passages": len(passages),
                       "expected_passage_found": found(passages, item["cite"]) if item["answerable"] else None}
                if writer is not None:
                    row.update(_write(item, passages, writer, usage))
                rows.append(row)
            for item in data.get("combined", []):
                passages = store.search(item["question"])
                row = {"id": item["id"], "answerable": True, "combined": True, "passages": len(passages),
                       "expected_passage_found": found(passages, item["cite"])}
                if writer is not None and chat is not None:
                    row.update(_combined(item, store, writer, usage, chat))
                rows.append(row)
        finally:
            store.close()
    return rows


def _combined(item, store, writer, usage, chat) -> dict:
    from agent.hybrid import HybridAssistant, load_route_vocabulary
    from question_router import QuestionRouter
    from tools.run_routing_benchmark import live_database_agent

    agent, neo4j = live_database_agent(chat)
    try:
        # The figures the database returns on its own for its half of the
        # question (as the application asks it), to compare with the combined answer.
        from question_router import database_part
        alone = agent.ask(database_part(item["question"], load_route_vocabulary()) or item["question"])
        figures = list(alone._figures)
        hybrid = HybridAssistant(agent, QuestionRouter(load_route_vocabulary()), ACCESS,
                                 document_retriever=store._retriever, has_documents=lambda tenant: True,
                                 answer_writer=writer)
        if usage:
            usage.take()
        started = time.perf_counter()
        answer = hybrid.ask(item["question"])
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        calls, tokens = usage.take() if usage else (0, 0)
    finally:
        neo4j.close()
    text = answer.answer
    missing = [p for p in item["must_include"] if not contains(text, p)]
    lost = [f for f in figures if f not in text]
    correct = (answer.route == "both" and answer.answer_basis == "written" and bool(figures)
               and not missing and not lost)
    return {"status": answer.answer_basis, "route": answer.route, "answer": text, "figures": figures,
            "figures_missing": lost, "missing": missing, "forbidden_present": [], "dropped": answer.warnings,
            "correct": correct, "write_ms": elapsed, "ai_calls": calls, "tokens": tokens}


def _write(item, passages, writer, usage) -> dict:
    from grounded_answers import Passage

    if usage:
        usage.take()
    started = time.perf_counter()
    written = writer.write(item["question"], [Passage(text=t, source=c) for t, c in passages])
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    calls, tokens = usage.take() if usage else (0, 0)
    text = written.text
    cited = sorted({n for s in written.sentences for n in s.sources})
    if item["answerable"]:
        missing = [p for p in item["must_include"] if not contains(text, p)]
        present = [p for p in item["must_exclude"] if contains(text, p)]
        correct = (written.status == "written" and not missing and not present
                   and cites(passages, cited, item["cite"]))
    else:
        missing = []
        present = [p for p in item["must_exclude"] if contains(text, p)]
        correct = written.status != "written"
    return {"status": written.status, "answer": text, "cited": cited, "dropped": list(written.dropped),
            "missing": missing, "forbidden_present": present, "correct": correct,
            "write_ms": elapsed, "ai_calls": calls, "tokens": tokens}


def summarise(report: dict) -> str:
    rows = report["rows"]
    answerable = [r for r in rows if r["answerable"]]
    lines = [f"Document answer evaluation ({report['mode']}), {len(rows)} questions",
             f"Search found the expected passage: {sum(r['expected_passage_found'] for r in answerable)}"
             f"/{len(answerable)} answerable questions"]
    for r in answerable:
        if not r["expected_passage_found"]:
            lines.append(f"  {r['id']}: expected passage not among the top {PASSAGE_LIMIT}")
    if report["mode"] == "live":
        lines.append(f"Correct: {sum(r['correct'] for r in rows)}/{len(rows)}")
        for r in rows:
            if not r["correct"]:
                lines.append(f"  {r['id']}: status={r['status']} missing={r['missing']} "
                             f"forbidden={r['forbidden_present']} answer={r['answer']!r} dropped={r['dropped']}")
        for r in rows:
            if r.get("figures_missing"):
                lines.append(f"  {r['id']}: database figure(s) not in the answer: {r['figures_missing']}")
        statuses = {s: sum(r["status"] == s for r in rows)
                    for s in ("written", "quoted", "not_answered", "unsupported", "failed")}
        lines.append(f"Outcomes: {statuses}; sentences left out by the checks: {sum(len(r['dropped']) for r in rows)}")
        times = [r["write_ms"] for r in rows if r["ai_calls"]]
        if times:
            ordered = sorted(times)
            lines.append(f"Writing time: p50={statistics.median(times):.0f} ms "
                         f"p95={ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]:.0f} ms; "
                         f"tokens={sum(r['tokens'] for r in rows)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="search only: no AI, no cost")
    mode.add_argument("--live", action="store_true", help="write answers with the AI model (costs tokens)")
    args = parser.parse_args(argv)

    data = load()
    writer = usage = chat = None
    if args.live:
        from answering.grounded_drafter import chat_drafter
        from ai.providers.openai_chat import OpenAIChatModel
        from config import settings
        from grounded_answers import GroundedAnswerWriter
        from tools.run_routing_benchmark import LLMUsage

        usage = LLMUsage()
        logging.getLogger("uvicorn.error").addHandler(usage)
        logging.getLogger("uvicorn.error").setLevel(logging.INFO)
        chat = OpenAIChatModel(timeout_seconds=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries)
        writer = GroundedAnswerWriter(chat_drafter(chat))

    report = {"mode": "live" if args.live else "dry-run", "questions_file": QUESTIONS.name,
              "run_at": datetime.now(timezone.utc).isoformat(), "rows": evaluate(data, writer, usage, chat)}
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"answers-{report['mode']}-{stamp}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(summarise(report))
    print(f"Report: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
