from typing import Literal
from pydantic import Field, model_validator
from .base import Contract, Identifier, Text

class ErrorResponse(Contract):
    request_id: Identifier
    code: Literal['invalid_request', 'unauthenticated', 'forbidden', 'not_found', 'unsupported', 'deadline_exceeded', 'rate_limited', 'dependency_unavailable', 'internal_error']
    message: Text
    retryable: bool = False
    retry_after_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def retry_hint(self):
        if self.retry_after_seconds is not None and not self.retryable:
            raise ValueError('Retry hint requires retryable error')
        return self
