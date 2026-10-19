from enum import StrEnum
from pydantic import model_validator
from .base import Contract, Identifier, Text

class Route(StrEnum):
    SQL = "text_to_sql"
    DOCUMENT = "document_rag"
    GRAPH = "graph_rag"

class RetrievalCapability(Contract):
    route: Route
    enabled: bool
    supported_facts: tuple[Text, ...] = ()

class RoutingStep(Contract):
    step_id: Identifier
    route: Route
    purpose: Text
    depends_on: tuple[Identifier, ...] = ()

class RoutingPlan(Contract):
    steps: tuple[RoutingStep, ...]

    @model_validator(mode="after")
    def valid_dependency_order(self):
        if not self.steps:
            raise ValueError("Routing plan must contain at least one step")
        seen = set()
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError("Duplicate step ID")
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError("Duplicate dependency")
            if not set(step.depends_on) <= seen:
                raise ValueError("Dependencies must refer to earlier steps")
            seen.add(step.step_id)
        return self
