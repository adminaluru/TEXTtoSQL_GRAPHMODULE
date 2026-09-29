from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class CompiledQuery(BaseModel):
    """
    Output of the deterministic SQL compiler.

    SQL text and runtime values are deliberately
    kept separate.

    Example:

        SQL:
            WHERE t0.name = %s

        parameters:
            ["Blue Prism"]
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    sql: str

    parameters: list[Any] = Field(
        default_factory=list
    )