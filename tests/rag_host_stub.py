"""A representative RAG host that knows only the public contract.

Imports nothing from FlowOne except `hybrid_contracts` (checked by
test_sql_retriever.py). It routes a question to whichever Retriever it was
given and turns the result into the text it would show a user.
"""
from hybrid_contracts import RetrievalRequest, RetrievalResult, Retriever


def answer_with(retriever: Retriever, request: RetrievalRequest) -> tuple[RetrievalResult, str]:
    result = retriever.retrieve(request)
    result.validate_for(request)
    if result.status == "complete":
        facts = "; ".join(e.content for e in result.evidence)
        sources = ", ".join(s.source_id for e in result.evidence for s in e.sources)
        return result, f"{facts} [sources: {sources}]"
    if result.status == "clarification" and result.clarification:
        options = " / ".join(o.label for o in result.clarification.options)
        return result, f"{result.clarification.question} {options}"
    return result, f"[{result.status}] " + " ".join(result.warnings)
