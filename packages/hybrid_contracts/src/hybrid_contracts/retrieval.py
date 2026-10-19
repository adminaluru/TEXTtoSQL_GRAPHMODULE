from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .base import Contract, Identifier, Text
from .clarification import ClarificationPrompt, ClarificationResponse
from .identity import AccessContext
from .routing import Route
from .evidence import EvidenceItem
from .tables import ResultTable

# complete      evidence found
# partial       some evidence; warnings say what is missing
# empty         the question was answered and nothing matched
# clarification the question must be narrowed or restated first
# unsupported   understood, but this capability cannot answer it faithfully
# denied        the caller is not permitted (no detail about what exists)
# disabled      the capability is switched off in this deployment
# unavailable   a dependency is down; retrying later may succeed
# failed        an unexpected error
RetrievalStatus = Literal["complete", "partial", "empty", "clarification", "unsupported",
                          "denied", "disabled", "unavailable", "failed"]

NO_CONTENT = {"empty", "clarification", "unsupported", "denied", "disabled", "unavailable", "failed"}
EXPLAINED = {"partial", "clarification", "unsupported", "denied", "disabled", "unavailable", "failed"}


class RetrievalRequest(Contract):
    request_id: Identifier
    question: Text = Field(max_length=5000)
    access: AccessContext
    route: Route
    deadline_at: AwareDatetime
    entity_ids: tuple[Identifier, ...] = ()
    max_evidence_items: int = Field(default=20, ge=1, le=100)
    # Binds clarification choices to one conversation; without it a
    # retriever may only ask for a restatement, not offer choices.
    conversation_id: Identifier | None = None
    # Continues an earlier clarification; the retriever re-checks that it
    # belongs to this caller and conversation.
    clarification_response: ClarificationResponse | None = None


class RetrievalResult(Contract):
    request_id: Identifier
    tenant_id: Identifier
    route: Route
    status: RetrievalStatus
    evidence: tuple[EvidenceItem, ...] = ()
    tables: tuple[ResultTable, ...] = ()
    clarification: ClarificationPrompt | None = None
    warnings: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def validate_result(self):
        if self.status in NO_CONTENT and (self.evidence or self.tables):
            raise ValueError("This status cannot carry evidence")
        if self.status == "complete" and not self.evidence:
            raise ValueError("Use empty for successful retrieval without evidence")
        if self.status in EXPLAINED and not self.warnings:
            raise ValueError("Incomplete results require a safe explanation")
        if self.clarification is not None and self.status != "clarification":
            raise ValueError("Clarification payload requires clarification status")
        if any(e.tenant_id != self.tenant_id for e in self.evidence):
            raise ValueError("Mixed tenant evidence is forbidden")
        ids = [e.evidence_id for e in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate evidence IDs")
        if len({t.table_id for t in self.tables}) != len(self.tables):
            raise ValueError("Duplicate table ID")
        for table in self.tables:
            for row in table.rows:
                if not set(row.evidence_ids) <= set(ids):
                    raise ValueError("Table row references missing evidence")
        return self

    def validate_for(self, request: RetrievalRequest) -> None:
        if (self.request_id, self.tenant_id, self.route) != (request.request_id, request.access.tenant_id, request.route):
            raise ValueError("Result does not match its request scope")
        if len(self.evidence) > request.max_evidence_items:
            raise ValueError("Evidence budget exceeded")
