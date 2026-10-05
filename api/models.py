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

    detail is a generic, client-safe message.
    request_id correlates the error with server logs.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    detail: str

    request_id: str | None = None