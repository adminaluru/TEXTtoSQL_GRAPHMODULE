"""Step 11 routing benchmark, offline part (docs/benchmarks/routing_questions.v1.json).

Checks the fixed rules against the labelled questions with no AI call:
- every question labelled "clear" is routed by rules alone, to its label;
- every simple database question avoids documents and the classifier.
The "unsure" questions are measured live with tools/run_routing_benchmark.py
(they need the AI classifier).
"""
import json
from pathlib import Path

import pytest
from hybrid_contracts import Route

from agent.hybrid import load_route_vocabulary
from question_router import QuestionRouter, classify

BENCHMARK = json.loads((Path(__file__).parents[1] / "docs" / "benchmarks" / "routing_questions.v1.json")
                       .read_text(encoding="utf-8"))
QUESTIONS = BENCHMARK["questions"]
VOCABULARY = load_route_vocabulary()
NAMES = {"database": Route.SQL, "documents": Route.DOCUMENT}
ALL = {Route.SQL, Route.DOCUMENT}


def test_benchmark_file_is_well_formed():
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids)) == 30
    assert all(q["rules"] in ("clear", "unsure") and set(q["expected"]) <= set(NAMES) and q["expected"]
               for q in QUESTIONS)


@pytest.mark.parametrize("item", [q for q in QUESTIONS if q["rules"] == "clear"], ids=lambda q: q["id"])
def test_clear_questions_are_routed_correctly_by_rules_alone(item):
    verdict = classify(item["question"], VOCABULARY)
    assert verdict.clear, f"rules unsure; signals {verdict.signals}"
    assert verdict.routes == {NAMES[e] for e in item["expected"]}, verdict.signals


@pytest.mark.parametrize("item", [q for q in QUESTIONS if q["rules"] == "unsure"], ids=lambda q: q["id"])
def test_unsure_questions_are_left_to_the_classifier(item):
    assert not classify(item["question"], VOCABULARY).clear


def test_simple_database_questions_never_reach_documents_or_the_classifier():
    calls = []
    router = QuestionRouter(VOCABULARY, classifier=lambda q: calls.append(q) or None)
    for item in QUESTIONS:
        if item["expected"] == ["database"]:
            plan = router.plan(item["question"], ALL)
            assert plan.routes == (Route.SQL,), item["id"]
    assert calls == []
