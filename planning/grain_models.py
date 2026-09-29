from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class GrainDecision(BaseModel):
    """
    Dynamic grain decision.

    No table-specific business rules are stored here.

    result_concepts:
        Entity/entities represented by the final answer.

    working_concept:
        Finest business entity at which the required
        calculation or evaluation operates.

    measure_concept:
        Business entity supplying an aggregate measure.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    result_concepts: list[str] = Field(
        default_factory=list
    )

    result_table_keys: list[str] = Field(
        default_factory=list
    )

    working_concept: str | None = None

    working_table_key: str | None = None

    measure_concept: str | None = None

    measure_table_key: str | None = None

    aggregation: str | None = None

    reason: str