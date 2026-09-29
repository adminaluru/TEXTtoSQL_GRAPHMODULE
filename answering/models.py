from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class AnswerResponse(BaseModel):
    """
    Final application-facing answer.

    The textual answer is accompanied by structured
    data so API/UI consumers do not need to parse text.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    answer: str

    result_type: Literal[
        "empty",
        "scalar",
        "tabular",
    ]

    row_count: int

    data: Any | None = None

    warnings: list[str] = Field(
        default_factory=list
    )