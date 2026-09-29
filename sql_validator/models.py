from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class SQLValidationResult(BaseModel):
    """
    Result returned by SQLValidator.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    valid: bool

    errors: list[str] = Field(
        default_factory=list
    )

    warnings: list[str] = Field(
        default_factory=list
    )