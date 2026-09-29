from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class GoldenEntity(BaseModel):
    """
    Expected business entity/filter contained in
    a golden question.

    Golden data is test data, so explicit expected
    values are intentional here.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    concept: str
    attribute: str
    value: str


class GoldenQuestion(BaseModel):
    """
    Canonical business-question test definition.

    Golden questions describe expected BUSINESS meaning,
    not generated SQL.

    Runtime components remain responsible for resolving:

    concept -> table
    attribute -> column
    relationships -> join path
    grain
    SQL
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    id: str

    question: str

    domain: str

    capability: Literal[
        "SUPPORTED",
        "SAFE_REJECT",
        "PLANNED",
    ]

    intent: Literal[
        "aggregate",
        "count",
        "list",
        "lookup",
        "unknown",
    ]

    concepts: list[str] = Field(
        default_factory=list
    )

    result_concept: str | None = None

    measure_concept: str | None = None

    measure_attribute: str | None = None

    group_by_concepts: list[str] = Field(
        default_factory=list
    )

    entities: list[
        GoldenEntity
    ] = Field(
        default_factory=list
    )

    aggregation: Literal[
        "SUM",
        "COUNT",
        "AVG",
        "MIN",
        "MAX",
    ] | None = None

    notes: str | None = None