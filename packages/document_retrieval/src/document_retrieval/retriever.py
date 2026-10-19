"""Document passages behind the portable Retriever contract (Step 9).

    question -> search words -> passages of the caller's workspace
             -> each passage checked again -> quoted evidence with citations

Rules
- The workspace comes from `request.access`, which the caller must have
  verified (as for the SQL retriever).
- The search is filtered by workspace, and every hit is checked again
  against the document directory: right workspace, document not deleted,
  passage from the version in use. Anything else is dropped, never shown.
- Passages are quoted exactly as stored. Nothing is summarised or reworded
  here, so nothing can be invented. They are untrusted text: a later step
  that hands them to a language model must treat them as data.

Status mapping:
    passages found                    -> complete
    no passage matches                -> empty (with an explanation)
    no searchable words in question   -> clarification
    caller not permitted              -> denied
    storage unavailable               -> unavailable
    deadline already passed / error   -> failed
"""
import logging
from datetime import datetime, timezone
from typing import Callable

from hybrid_contracts import (EvidenceItem, RetrievalRequest, RetrievalResult, Route,
                              SourceReference)

from .ports import DocumentDirectory, PassageSearch
from .terms import search_terms

logger = logging.getLogger(__name__)

NO_TERMS = "Use the words you expect to find in the document, for example “late payment fee”."
NO_MATCH = "No passage in this workspace's documents matches these words."
DEADLINE_EXCEEDED = "The search could not be completed within the requested time."
NOT_PERMITTED = "You are not permitted to search these documents."
UNAVAILABLE = "Document search is temporarily unavailable. Please try again later."
FAILED = "The search could not be completed because of an internal error."

# Ask storage for a few more than needed: some hits may be dropped by the checks.
_OVERFETCH = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentRetriever:
    route = Route.DOCUMENT

    def __init__(self, search: PassageSearch, directory: DocumentDirectory,
                 clock: Callable[[], datetime] = _utcnow):
        self._search = search
        self._directory = directory
        self._clock = clock

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if request.route != self.route:
            raise ValueError("Request was sent to the wrong retriever")
        if self._clock() >= request.deadline_at:
            return self._result(request, "failed", warnings=(DEADLINE_EXCEEDED,))

        terms = search_terms(request.question)
        if not terms:
            return self._result(request, "clarification", warnings=(NO_TERMS,))

        tenant = request.access.tenant_id
        try:
            hits = self._search.search(tenant, terms, request.max_evidence_items * _OVERFETCH)
            documents = self._directory.active_documents(tenant, sorted({h.document_id for h in hits}))
        except PermissionError:
            return self._result(request, "denied", warnings=(NOT_PERMITTED,))
        except (ConnectionError, TimeoutError, OSError):
            logger.warning("document_search_unavailable request_id=%s", request.request_id, exc_info=True)
            return self._result(request, "unavailable", warnings=(UNAVAILABLE,))
        except Exception:
            logger.exception("document_search_failed request_id=%s", request.request_id)
            return self._result(request, "failed", warnings=(FAILED,))

        if self._clock() >= request.deadline_at:
            return self._result(request, "failed", warnings=(DEADLINE_EXCEEDED,))

        retrieved_at = self._clock()
        evidence: list[EvidenceItem] = []
        dropped = 0
        for hit in hits:
            document = documents.get(hit.document_id)
            if hit.tenant_id != tenant or document is None or hit.version != document.active_version:
                dropped += 1
                continue
            evidence.append(EvidenceItem(
                evidence_id=f"{hit.document_id}:v{hit.version}:p{hit.ordinal}",
                tenant_id=tenant,
                kind="passage",
                content=hit.text,
                sources=(SourceReference(
                    source_id=hit.document_id,
                    tenant_id=tenant,
                    version=str(hit.version),
                    location=f"{document.filename} · {hit.location}",
                    kind="document",
                ),),
                retrieved_at=retrieved_at,
            ))
            if len(evidence) == request.max_evidence_items:
                break

        if dropped:
            # Normal right after a replacement or deletion; a cross-workspace
            # hit would be a storage bug, so the count is logged.
            logger.info("document_search_dropped request_id=%s count=%s", request.request_id, dropped)
        if not evidence:
            return self._result(request, "empty", warnings=(NO_MATCH,))
        return self._result(request, "complete", evidence=tuple(evidence))

    def _result(self, request: RetrievalRequest, status: str, evidence=(), warnings=()) -> RetrievalResult:
        result = RetrievalResult(request_id=request.request_id, tenant_id=request.access.tenant_id,
                                 route=self.route, status=status, evidence=evidence, warnings=warnings)
        result.validate_for(request)
        return result
