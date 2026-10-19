from typing import Protocol
from .retrieval import RetrievalRequest, RetrievalResult
from .routing import Route

class Retriever(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class DisabledRetriever:
    """Stand-in for a capability that is switched off.

    Always answers truthfully with status "disabled"; never evidence or a
    placeholder success. Use it instead of leaving a route unwired.
    """

    def __init__(self, route: Route, reason: str = "This capability is not enabled."):
        if not reason.strip():
            raise ValueError("A disabled retriever needs a reason")
        self._route = route
        self._reason = reason

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if request.route != self._route:
            raise ValueError("Request was sent to the wrong retriever")
        return RetrievalResult(request_id=request.request_id, tenant_id=request.access.tenant_id,
                               route=self._route, status="disabled", warnings=(self._reason,))
