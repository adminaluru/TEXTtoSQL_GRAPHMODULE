from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class PostgreSQLExecutionResult(BaseModel):
    """
    Controlled result returned by PostgreSQLExecutor.

    Database connection/cursor objects never escape
    this execution boundary.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    columns: list[str] = Field(
        default_factory=list
    )

    rows: list[
        dict[str, Any]
    ] = Field(
        default_factory=list
    )

    row_count: int

    truncated: bool = False

    execution_ms: float