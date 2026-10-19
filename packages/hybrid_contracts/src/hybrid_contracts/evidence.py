from decimal import Decimal
from typing import Annotated, Literal
from pydantic import AwareDatetime, Field, model_validator
from .base import Contract, Identifier, Text

class Money(Contract):
    amount: Decimal = Field(allow_inf_nan=False)
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]

class SourceReference(Contract):
    source_id: Identifier
    tenant_id: Identifier
    version: Identifier
    location: Text
    kind: Literal["database", "document", "graph"]

class EvidenceItem(Contract):
    evidence_id: Identifier
    tenant_id: Identifier
    kind: Literal["structured_fact", "passage", "relationship"]
    content: Text
    entity_ids: tuple[Identifier, ...] = ()
    sources: tuple[SourceReference, ...]
    retrieved_at: AwareDatetime
    observed_at: AwareDatetime | None = None
    monetary_value: Money | None = None

    @model_validator(mode="after")
    def validate_sources(self):
        if not self.sources:
            raise ValueError("Evidence requires provenance")
        if any(s.tenant_id != self.tenant_id for s in self.sources):
            raise ValueError("Evidence sources must belong to its tenant")
        return self
