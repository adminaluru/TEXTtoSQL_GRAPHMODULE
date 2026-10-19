"""Choose the minimal set of sources for one question (decision D-011).

    1. Rules (no AI call). Clear wording decides.
    2. Unsure: one call to the classifier, when one is configured.
    3. Still unclear (no classifier, it failed, or it said "unclear"):
       ask both the database and the documents (owner choice in D-011).
    4. Routes that are not available are removed, with a note:
       documents when the workspace has none ready; graph until Step 10.

The plan never adds a route because another one failed: a database question
that cannot be answered is not quietly turned into a document search.
"""
from dataclasses import dataclass
from typing import Callable, Iterable

from hybrid_contracts import Route

from .rules import RouteVocabulary, classify

# Order in which steps are listed (they run in parallel when independent).
_ORDER = (Route.SQL, Route.DOCUMENT)

NO_DOCUMENTS = "This workspace has no searchable documents yet, so documents were not searched."
DOCUMENTS_ONLY_NONE = "This looks like a question about your documents, but this workspace has no searchable documents yet."

# A classifier returns the routes it chose, or None when it cannot tell.
Classifier = Callable[[str], frozenset[Route] | None]


@dataclass(frozen=True)
class RoutePlan:
    routes: tuple[Route, ...]
    # "rules", "classifier" or "unclear_both"
    method: str
    classifier_calls: int
    signals: tuple[str, ...]
    notes: tuple[str, ...] = ()


class QuestionRouter:
    def __init__(self, vocabulary: RouteVocabulary, classifier: Classifier | None = None):
        self._vocabulary = vocabulary
        self._classifier = classifier

    def plan(self, question: str, available: Iterable[Route]) -> RoutePlan:
        available = frozenset(available)
        verdict = classify(question, self._vocabulary)
        routes, method, calls = verdict.routes, "rules", 0

        if not verdict.clear:
            chosen = None
            if self._classifier is not None:
                calls = 1
                try:
                    chosen = self._classifier(question)
                except Exception:
                    chosen = None
            if chosen:
                routes, method = frozenset(chosen) & frozenset(_ORDER), "classifier"
            if not routes:
                routes, method = frozenset(_ORDER), "unclear_both"

        notes = []
        if Route.DOCUMENT in routes and Route.DOCUMENT not in available:
            routes = routes - {Route.DOCUMENT}
            notes.append(NO_DOCUMENTS if routes else DOCUMENTS_ONLY_NONE)
        routes = routes & available
        return RoutePlan(tuple(r for r in _ORDER if r in routes), method, calls, verdict.signals, tuple(notes))
