from typing import Literal
from pydantic import model_validator
from .base import Contract, Identifier, Text
from .evidence import EvidenceItem
from .tables import ResultTable
from .clarification import ClarificationPrompt
from .errors import ErrorResponse

class Citation(Contract):
    citation_id: Identifier
    evidence_id: Identifier
    source_id: Identifier

class AnswerResult(Contract):
    request_id: Identifier
    tenant_id: Identifier
    status: Literal["answered", "partial", "clarification", "insufficient_evidence", "failed"]
    answer: Text
    evidence: tuple[EvidenceItem, ...] = ()
    citations: tuple[Citation, ...] = ()
    limitations: tuple[Text, ...] = ()
    tables: tuple[ResultTable, ...] = ()
    clarification: ClarificationPrompt | None = None
    error: ErrorResponse | None = None

    @model_validator(mode="after")
    def validate_references(self):
        by_id = {e.evidence_id: e for e in self.evidence}
        if len(by_id) != len(self.evidence):
            raise ValueError("Duplicate evidence IDs")
        if any(e.tenant_id != self.tenant_id for e in self.evidence):
            raise ValueError("Mixed tenant evidence is forbidden")
        if self.status in {"answered", "partial"} and not self.evidence:
            raise ValueError("An answer requires evidence")
        if self.status in {"partial", "insufficient_evidence", "failed"} and not self.limitations:
            raise ValueError("This status requires limitations")
        if len({c.citation_id for c in self.citations}) != len(self.citations):
            raise ValueError("Duplicate citation IDs")
        for c in self.citations:
            e = by_id.get(c.evidence_id)
            if e is None or c.source_id not in {s.source_id for s in e.sources}:
                raise ValueError("Citation must reference included evidence and source")
        if self.clarification is not None and self.status != 'clarification':
            raise ValueError('Clarification payload requires clarification status')
        if self.error is not None and (self.status != 'failed' or self.error.request_id != self.request_id):
            raise ValueError('Error must belong to this failed request')
        if self.tables and self.status not in {'answered', 'partial'}:
            raise ValueError('Tables require an answer status')
        if len({t.table_id for t in self.tables}) != len(self.tables):
            raise ValueError('Duplicate table ID')
        for table in self.tables:
            for row in table.rows:
                if not set(row.evidence_ids) <= set(by_id):
                    raise ValueError('Table row references missing evidence')
        return self
