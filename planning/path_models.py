from pydantic import BaseModel, ConfigDict

from ontology.models import PhysicalPath


class RankedPhysicalPath(BaseModel):
    """
    A physical-path candidate together with
    deterministic ranking information.

    Lower score means a better candidate.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    path: PhysicalPath

    score: int
    hop_count: int

    confirmed_joins: int = 0
    warning_joins: int = 0
    inferred_joins: int = 0