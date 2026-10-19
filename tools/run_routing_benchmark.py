"""Routing benchmark for Step 11 (decision D-011).

Runs the labelled questions in docs/benchmarks/routing_questions.v1.json and
reports, per question and in total:
    route chosen vs expected, how it was decided (rules / classifier / both),
    AI calls and tokens, and end-to-end time (p50 / p95), cold and warm.

It compares the original database-only path with routing on the database
questions, so the extra cost of routing is visible.

    python -m tools.run_routing_benchmark --dry-run   rules only; no AI, no database, no cost
    python -m tools.run_routing_benchmark --live      real AI model and local databases (costs tokens)

Live mode uses tenant-acme, the synthetic tenant mapping, and its own
synthetic policy documents in a temporary folder; it never reads or changes
uploaded documents. The report is written to docs/benchmarks/results/.
"""
import argparse
import json
import logging
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from hybrid_contracts import AccessContext, Route

ROOT = Path(__file__).parents[1]
QUESTIONS = ROOT / "docs" / "benchmarks" / "routing_questions.v1.json"
RESULTS = ROOT / "docs" / "benchmarks" / "results"
NAMES = {"database": Route.SQL, "documents": Route.DOCUMENT}
ACCESS = AccessContext(subject_id="routing-benchmark", tenant_id="tenant-acme", policy_version="benchmark")

SYNTHETIC_POLICY = [
    ("Late payment fees", "Late payments incur a fee of 2% per month on the overdue amount."),
    ("Reminders", "Customers receive a payment reminder 7 days before and 3 days after the due date."),
    ("Disputes", "A dispute must name the invoice and the claim amount. The collections team responds within 10 business days."),
    ("Write-offs", "Write-offs above 10,000 USD must be approved by the Credit Manager."),
    ("Follow-up", "Follow up with a customer 5 days after a broken promise to pay."),
]
SYNTHETIC_TERMS = "Payment terms: net 30. Payment is due 30 days after the invoice date."


def percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return round(ordered[index], 1)


class LLMUsage(logging.Handler):
    """Counts AI calls and tokens from the FLOWONE_OPENAI_PERF log lines."""

    _TOKENS = re.compile(r"\btotal_tokens=(\d+)")

    def __init__(self):
        super().__init__(logging.INFO)
        self.calls = 0
        self.tokens = 0

    def emit(self, record):
        message = record.getMessage()
        if message.startswith("FLOWONE_OPENAI_PERF"):
            self.calls += 1
            match = self._TOKENS.search(message)
            self.tokens += int(match.group(1)) if match else 0

    def take(self) -> tuple[int, int]:
        calls, tokens = self.calls, self.tokens
        self.calls = self.tokens = 0
        return calls, tokens


def dry_run(questions) -> dict:
    from agent.hybrid import load_route_vocabulary
    from question_router import QuestionRouter

    router = QuestionRouter(load_route_vocabulary())
    rows = []
    for item in questions:
        plan = router.plan(item["question"], {Route.SQL, Route.DOCUMENT})
        expected = {NAMES[e] for e in item["expected"]}
        rows.append({"id": item["id"], "expected": item["expected"],
                     "routes": [r.value for r in plan.routes], "method": plan.method,
                     "correct": set(plan.routes) == expected})
    return {"mode": "dry-run", "rows": rows}


def live(questions, passes: int) -> dict:
    from adapters.document_files import LocalFolderFileStore
    from adapters.document_search import SqliteDocumentDirectory, SqlitePassageSearch
    from adapters.document_sqlite import SqliteDatabase, SqliteDocumentCatalog, SqlitePassageIndex
    from agent.factory import build_research_agent
    from agent.hybrid import HybridAssistant, load_route_vocabulary
    from ai.providers.openai_chat import OpenAIChatModel
    from api.identity_setup import tenant_resolver_from_file
    from clarification_state import PendingSelectionStore
    from config import settings
    from database.neo4j import Neo4jClient
    from document_ingestion import DocumentIngestionService
    from document_retrieval import DocumentRetriever
    from execution.postgres_executor import PostgreSQLExecutor
    from nlu.route_classifier import LLMRouteClassifier
    from ontology.cache import TTLCache
    from planning.entity_choice_labels import load_choice_labels
    from planning.tenant_entity_scope import TenantEntityScopePolicy
    from question_router import QuestionRouter
    from tests.document_test_files import make_docx, make_pdf

    usage = LLMUsage()
    logging.getLogger("uvicorn.error").addHandler(usage)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    chat = OpenAIChatModel(timeout_seconds=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries)
    neo4j = Neo4jClient()
    executor = PostgreSQLExecutor(statement_timeout_ms=5000, max_rows=100)
    cache = TTLCache(ttl_seconds=settings.ontology_cache_ttl_seconds)
    resolver = tenant_resolver_from_file(str(ROOT / "docs" / "examples" / "tenant_scope.dev.json"))
    agent = build_research_agent(
        chat_model=chat, neo4j_client=neo4j, postgres_executor=executor, ontology_cache=cache,
        max_repair_attempts=settings.agent_repair_max_attempts,
        repair_budget_seconds=settings.agent_repair_budget_seconds,
        request_timeout_seconds=settings.request_timeout_seconds,
        entity_scope_policy=TenantEntityScopePolicy(resolver, ACCESS),
        entity_choice_labels=load_choice_labels(), clarification_store=PendingSelectionStore())

    with tempfile.TemporaryDirectory(prefix="flowone-routing-") as folder:
        database = SqliteDatabase(Path(folder) / "catalog.sqlite3")
        queue: list[str] = []
        service = DocumentIngestionService(SqliteDocumentCatalog(database), LocalFolderFileStore(Path(folder) / "files"),
                                           SqlitePassageIndex(database), enqueue=queue.append)

        def policy(document):
            document.add_heading("Credit and collections policy", level=0)
            for heading, text in SYNTHETIC_POLICY:
                document.add_heading(heading, level=1)
                document.add_paragraph(text)
        service.upload(ACCESS, "credit_policy.docx", make_docx(policy))
        service.upload(ACCESS, "payment_terms.pdf", make_pdf([SYNTHETIC_TERMS]))
        while queue:
            service.process(queue.pop(0))

        directory = SqliteDocumentDirectory(database)
        hybrid = HybridAssistant(agent, QuestionRouter(load_route_vocabulary(), LLMRouteClassifier(chat)), ACCESS,
                                 document_retriever=DocumentRetriever(SqlitePassageSearch(database), directory),
                                 has_documents=directory.has_searchable,
                                 request_timeout_seconds=settings.request_timeout_seconds)

        rows = []
        for run in range(1, passes + 1):
            condition = "cold" if run == 1 else "warm"
            for item in questions:
                expected = {NAMES[e] for e in item["expected"]}
                if expected == {Route.SQL}:
                    rows.append(measure("original", condition, item, lambda q: agent.ask(q), usage))
                rows.append(measure("routing", condition, item, hybrid.ask, usage, expected))
        database.close()
    neo4j.close()
    return {"mode": "live", "passes": passes, "rows": rows}


def measure(mode, condition, item, ask, usage, expected=None) -> dict:
    usage.take()
    started = time.perf_counter()
    outcome, trace = "error", {}
    try:
        answer = ask(item["question"])
        outcome = answer.result_type
        trace = answer._route_trace or {}
    except Exception as exc:
        outcome = f"error:{type(exc).__name__}"
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    calls, tokens = usage.take()
    row = {"id": item["id"], "mode": mode, "condition": condition, "expected": item["expected"],
           "outcome": outcome, "total_ms": elapsed, "ai_calls": calls, "tokens": tokens}
    if mode == "routing":
        chosen = {"database": {Route.SQL}, "documents": {Route.DOCUMENT},
                  "both": {Route.SQL, Route.DOCUMENT}}.get(trace.get("route"), set())
        row.update(route=trace.get("route"), method=trace.get("method"),
                   classifier_calls=trace.get("classifier_calls"), timings_ms=trace.get("timings_ms"),
                   correct=chosen == expected)
    return row


def _chosen(row: dict) -> set:
    if "routes" in row:
        return {Route(value) for value in row["routes"]}
    return {"database": {Route.SQL}, "documents": {Route.DOCUMENT},
            "both": {Route.SQL, Route.DOCUMENT}}.get(row.get("route"), set())


def summarise(report: dict) -> str:
    rows = report["rows"]
    lines = [f"Routing benchmark ({report['mode']}), {len({r['id'] for r in rows})} questions"]
    routed = [r for r in rows if r.get("mode", "routing") == "routing"]
    if routed:
        correct = sum(1 for r in routed if r["correct"])
        lines.append(f"Route exactly right: {correct}/{len(routed)}")
        for r in routed:
            if r["correct"]:
                continue
            chosen = _chosen(r)
            expected = {NAMES[e] for e in r["expected"]}
            # An extra source costs time; a missing one can give a wrong or empty answer.
            kind = "extra source searched" if expected < chosen else "needed source MISSED"
            lines.append(f"  {r['id']}: {kind} (expected {r['expected']}, got {sorted(x.value for x in chosen)})")
    if report["mode"] == "live":
        for mode in ("original", "routing"):
            for condition in ("cold", "warm"):
                subset = [r for r in rows if r["mode"] == mode and r["condition"] == condition]
                if not subset:
                    continue
                times = [r["total_ms"] for r in subset]
                lines.append(
                    f"{mode:8} {condition}: n={len(subset)} p50={percentile(times, .5)} ms "
                    f"p95={percentile(times, .95)} ms stdev={round(statistics.pstdev(times), 1)} ms "
                    f"ai_calls={sum(r['ai_calls'] for r in subset)} tokens={sum(r['tokens'] for r in subset)} "
                    f"errors={sum(1 for r in subset if r['outcome'].startswith('error'))}")
        db = [r for r in rows if r["expected"] == ["database"]]
        for condition in ("cold", "warm"):
            original = [r for r in db if r["mode"] == "original" and r["condition"] == condition]
            routing = [r for r in db if r["mode"] == "routing" and r["condition"] == condition]
            if original and routing:
                lines.append(
                    f"database questions {condition}: extra AI calls from routing = "
                    f"{sum(r['ai_calls'] for r in routing) - sum(r['ai_calls'] for r in original)}, "
                    f"p50 {percentile([r['total_ms'] for r in original], .5)} -> "
                    f"{percentile([r['total_ms'] for r in routing], .5)} ms")
        by_route = {}
        for r in routed:
            by_route.setdefault(r.get("route"), []).append(r["total_ms"])
        for route, times in sorted(by_route.items(), key=lambda kv: str(kv[0])):
            lines.append(f"route {route}: n={len(times)} p50={percentile(times, .5)} ms p95={percentile(times, .95)} ms")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="rules only: no AI, no database, no cost")
    mode.add_argument("--live", action="store_true", help="real AI model and local databases (costs tokens)")
    parser.add_argument("--passes", type=int, default=2, help="live: 1 cold pass, then warm passes")
    args = parser.parse_args()

    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    report = dry_run(data["questions"]) if args.dry_run else live(data["questions"], max(1, args.passes))
    report.update(benchmark_version=data["version"], run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    summary = summarise(report)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"routing-{report['mode']}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps({**report, "summary": summary}, indent=2), encoding="utf-8")
    sys.stdout.write(summary + f"\nReport: {out.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
