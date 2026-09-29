from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class AskRequest(BaseModel):
    """
    HTTP request contract for POST /ask.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    question: str = Field(
        min_length=1,
        max_length=5000,
    )


class APIErrorResponse(BaseModel):
    """
    Stable API error contract.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    error: str

    detail: str | None = None