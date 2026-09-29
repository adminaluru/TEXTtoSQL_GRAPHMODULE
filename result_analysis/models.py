from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class ResultAnalysis(BaseModel):
    """
    Structured interpretation of PostgreSQL output.

    This layer does not generate natural-language answers.

    result_type:

        empty
            No result rows.

        scalar
            One logical aggregate value.

        tabular
            Multi-column and/or multi-row result.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    result_type: Literal[
        "empty",
        "scalar",
        "tabular",
    ]

    columns: list[str] = Field(
        default_factory=list
    )

    row_count: int

    truncated: bool = False

    scalar_alias: str | None = None

    scalar_value: Any | None = None

    rows: list[
        dict[str, Any]
    ] = Field(
        default_factory=list
    )

    warnings: list[str] = Field(
        default_factory=list
    )