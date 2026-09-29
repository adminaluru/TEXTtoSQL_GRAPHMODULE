from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class GuardrailResult(BaseModel):
    """
    Result of execution-policy evaluation.

    allowed:
        True only when the compiled query is safe
        enough to proceed to PostgreSQL execution.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    allowed: bool

    violations: list[str] = Field(
        default_factory=list
    )

    warnings: list[str] = Field(
        default_factory=list
    )