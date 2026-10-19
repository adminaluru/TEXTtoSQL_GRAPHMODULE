from enum import StrEnum
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .base import Contract, Identifier, Text

class DocumentState(StrEnum):
    QUEUED='queued'
    PROCESSING='processing'
    REVIEW_REQUIRED='review_required'
    READY='ready'
    FAILED='failed'
    DELETING='deleting'
    DELETED='deleted'

class UploadMetadata(Contract):
    """Public metadata only. Actual bytes, file type and size require server checks."""
    filename: Text = Field(max_length=255)
    file_type: Literal['docx', 'pdf', 'xlsx']
    size_bytes: int = Field(gt=0)

    @model_validator(mode='after')
    def filename_matches(self):
        if '/' in self.filename or '\\' in self.filename or '\x00' in self.filename:
            raise ValueError('Filename must be a basename')
        if not self.filename.lower().endswith('.'+self.file_type):
            raise ValueError('Filename extension does not match declared type')
        return self

class DocumentJob(Contract):
    job_id: Identifier
    tenant_id: Identifier
    source_id: Identifier
    source_version: Identifier
    state: DocumentState
    created_at: AwareDatetime
    updated_at: AwareDatetime
    progress_percent: int = Field(default=0, ge=0, le=100)
    attempts: int = Field(default=0, ge=0)
    message: Text | None = None

    @model_validator(mode='after')
    def valid_state(self):
        if self.updated_at < self.created_at:
            raise ValueError('Update precedes creation')
        if self.state == DocumentState.READY and self.progress_percent != 100:
            raise ValueError('Ready job requires completed processing')
        if self.state in {DocumentState.FAILED, DocumentState.REVIEW_REQUIRED} and not self.message:
            raise ValueError('Failed or review job requires safe explanation')
        return self

    def can_transition_to(self, state: DocumentState) -> bool:
        transitions = {
            DocumentState.QUEUED: {DocumentState.PROCESSING, DocumentState.FAILED, DocumentState.DELETING},
            DocumentState.PROCESSING: {DocumentState.REVIEW_REQUIRED, DocumentState.READY, DocumentState.FAILED, DocumentState.DELETING},
            DocumentState.REVIEW_REQUIRED: {DocumentState.QUEUED, DocumentState.FAILED, DocumentState.DELETING},
            DocumentState.READY: {DocumentState.DELETING},
            DocumentState.FAILED: {DocumentState.QUEUED, DocumentState.DELETING},
            DocumentState.DELETING: {DocumentState.DELETED},
            DocumentState.DELETED: set(),
        }
        return state in transitions[self.state]
