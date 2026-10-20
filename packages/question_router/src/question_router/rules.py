"""Fixed routing rules: clear wording decides the route without any AI call.

    database  a measure ("how many", "total", "average"...) or a listing verb
              about a business record word ("invoices", "payments"...)
    documents a document word ("policy", "contract", "terms"...) or phrase
              ("according to", "what does ... say")
    both      both kinds of signal in one question, or a reason question
              ("why", "reason") about a business record: the record's data
              is in the database, the explanation is often in a document
    unsure    neither, or only weak signals: the router asks the classifier

The record words are the application's own vocabulary (passed in), so the
package itself knows no business terms.
"""
import re
from dataclasses import dataclass, field

from hybrid_contracts import Route

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# Where a two-part question splits: ";" or "and" before a new question word.
_CLAUSE_BREAK = re.compile(
    r"\s*(?:;|,?\s+and\s+(?=(?:what|how|which|who|when|where|why|is|are|does|do|did|can|list|show|tell)\b))\s*",
    re.IGNORECASE)

MEASURE_PHRASES = (
    "how many", "how much", "total", "sum", "count", "number of", "average", "mean", "maximum", "minimum",
    "highest", "lowest", "largest", "smallest", "top", "balance", "outstanding", "per customer",
    "by customer", "grouped by", "breakdown",
)
LIST_VERBS = ("list", "show", "which", "who")
DOCUMENT_WORDS = (
    "policy", "policies", "contract", "contracts", "agreement", "agreements", "terms", "clause", "clauses",
    "document", "documents", "procedure", "procedures", "guideline", "guidelines", "manual", "handbook",
    "playbook", "sop", "memo", "letter", "uploaded", "file", "files", "pdf", "spreadsheet", "section",
)
DOCUMENT_PHRASES = ("according to", "what does", "what do the", "mentioned in", "written in", "stated in")
REASON_WORDS = ("why", "reason", "reasons", "explain", "explanation")


@dataclass(frozen=True)
class RouteVocabulary:
    # Business record words of this application, e.g. "invoice", "payment".
    record_words: frozenset[str]
    measure_phrases: tuple[str, ...] = MEASURE_PHRASES
    list_verbs: tuple[str, ...] = LIST_VERBS
    document_words: frozenset[str] = field(default_factory=lambda: frozenset(DOCUMENT_WORDS))
    document_phrases: tuple[str, ...] = DOCUMENT_PHRASES
    reason_words: frozenset[str] = field(default_factory=lambda: frozenset(REASON_WORDS))


@dataclass(frozen=True)
class RuleVerdict:
    routes: frozenset[Route]
    clear: bool
    # Which signals were seen, for traces and the benchmark report.
    signals: tuple[str, ...]


def database_part(question: str, vocabulary: RouteVocabulary) -> str | None:
    """
    The database half of a two-part question, e.g. "How many invoices are
    overdue" from "How many invoices are overdue, and what does the policy say
    about late fees?". Only when every part is clearly one kind by the rules
    and both kinds occur; otherwise None (the whole question is used).
    """
    parts = [p.strip(" ,?") for p in _CLAUSE_BREAK.split(question) if p.strip(" ,?")]
    if len(parts) < 2:
        return None
    database, documents = [], []
    for part in parts:
        verdict = classify(part, vocabulary)
        if not verdict.clear or len(verdict.routes) != 1:
            return None
        (database if Route.SQL in verdict.routes else documents).append(part)
    if not database or not documents:
        return None
    joined = " and ".join(database)
    return joined[0].upper() + joined[1:] + "?"


def classify(question: str, vocabulary: RouteVocabulary) -> RuleVerdict:
    text = " ".join(_WORD.findall(question.casefold()))
    words = set(text.split())
    padded = f" {text} "

    def has_phrase(phrase: str) -> bool:
        return f" {phrase} " in padded

    record = sorted(w for w in words if w in vocabulary.record_words or w.rstrip("s") in vocabulary.record_words)
    measures = [p for p in vocabulary.measure_phrases if has_phrase(p)]
    listing = [v for v in vocabulary.list_verbs if has_phrase(v)]
    doc_words = sorted(words & vocabulary.document_words)
    doc_phrases = [p for p in vocabulary.document_phrases if has_phrase(p)]
    reasons = sorted(words & vocabulary.reason_words)

    data_signal = bool(record) and bool(measures or listing)
    document_signal = bool(doc_words or doc_phrases)
    signals = tuple(
        [f"record:{w}" for w in record] + [f"measure:{m}" for m in measures] + [f"list:{v}" for v in listing]
        + [f"document:{w}" for w in doc_words] + [f"document:{p}" for p in doc_phrases]
        + [f"reason:{w}" for w in reasons])

    if record and reasons:
        # "Why was invoice X adjusted?": skipping either source could drop
        # the figures or the written reason, so both are searched.
        return RuleVerdict(frozenset({Route.SQL, Route.DOCUMENT}), True, signals)

    if data_signal and document_signal:
        # "What does the policy say..." alone is a document question even if it
        # names a record; both are needed only when a measure is asked too.
        if measures:
            return RuleVerdict(frozenset({Route.SQL, Route.DOCUMENT}), True, signals)
        return RuleVerdict(frozenset({Route.DOCUMENT}), True, signals)
    if data_signal:
        return RuleVerdict(frozenset({Route.SQL}), True, signals)
    if document_signal:
        return RuleVerdict(frozenset({Route.DOCUMENT}), True, signals)
    return RuleVerdict(frozenset(), False, signals)
