from pydantic import AwareDatetime, Field, model_validator
from .base import Contract, Identifier, Text

class ClarificationOption(Contract):
    option_id: Identifier
    label: Text

class ClarificationPrompt(Contract):
    clarification_id: Identifier
    question: Text
    reason: Text
    options: tuple[ClarificationOption, ...] = ()
    allows_text: bool = False
    expires_at: AwareDatetime

    @model_validator(mode='after')
    def valid_options(self):
        if not self.options and not self.allows_text:
            raise ValueError('Clarification requires options or text input')
        if len({o.option_id for o in self.options}) != len(self.options):
            raise ValueError('Duplicate option ID')
        return self

class ClarificationResponse(Contract):
    """Public input; server resolves opaque IDs against owned pending state."""
    clarification_id: Identifier
    option_id: Identifier | None = None
    text: Text | None = Field(default=None, max_length=5000)

    @model_validator(mode='after')
    def one_response(self):
        if (self.option_id is None) == (self.text is None):
            raise ValueError('Supply exactly one option or text response')
        return self
