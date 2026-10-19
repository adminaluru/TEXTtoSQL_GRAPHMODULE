from typing import Literal
from pydantic import Field
from .base import Contract, Identifier, Text

class QuestionRequest(Contract):
    """Public input deliberately excludes identity, tenant, role and permissions."""
    question: Text = Field(max_length=5000)
    conversation_id: Identifier | None = None

class EntityReference(Contract):
    entity_type: Identifier
    value: Text
    entity_id: Identifier | None = None

class QuestionAnalysis(Contract):
    requested_facts: tuple[Text, ...] = ()
    entities: tuple[EntityReference, ...] = ()
    output_kind: Literal["calculation", "list", "explanation", "combined", "unknown"]
    needs_relationship_evidence: bool = False
    ambiguities: tuple[Text, ...] = ()
