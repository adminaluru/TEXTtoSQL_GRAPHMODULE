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

class DatabasePrivileges(BaseModel):
    """What the application's database login is allowed to do (Step 7)."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    role: str

    is_superuser: bool

    bypasses_row_security: bool

    # Tables in the queried schema the login could INSERT, UPDATE,
    # DELETE or TRUNCATE. Must be 0 in production.
    writable_tables: int

    can_create: bool
