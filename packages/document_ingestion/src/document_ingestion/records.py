"""What the service remembers about documents and their versions."""
from dataclasses import dataclass, replace
from datetime import datetime

from hybrid_contracts import DocumentJob

from .models import FileType


@dataclass(frozen=True)
class DocumentRecord:
    """One document in one workspace. Re-uploading the same file name adds a version."""

    document_id: str
    tenant_id: str
    filename: str
    uploaded_by: str
    created_at: datetime
    latest_version: int
    # The version whose passages are searchable; None until one is ready.
    active_version: int | None = None
    deleted: bool = False

    def with_(self, **changes) -> "DocumentRecord":
        return replace(self, **changes)


@dataclass(frozen=True)
class VersionRecord:
    """One uploaded file. Its processing job tracks queued -> processing -> ready/failed."""

    document_id: str
    version: int
    sha256: str
    size_bytes: int
    file_type: FileType
    file_key: str
    uploaded_by: str
    job: DocumentJob
    notes: tuple[str, ...] = ()
    passage_count: int = 0
    # Stable refusal code when processing failed for a reason in the file
    # (never retried), e.g. "no_readable_text".
    failure_code: str | None = None

    def with_(self, **changes) -> "VersionRecord":
        return replace(self, **changes)


@dataclass(frozen=True)
class UploadResult:
    document: DocumentRecord
    version: VersionRecord
    # True when the same file was already in this workspace: nothing new was stored.
    duplicate: bool = False
