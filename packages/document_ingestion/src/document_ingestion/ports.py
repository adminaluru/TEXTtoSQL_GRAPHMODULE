"""The storage the service needs, described as interfaces ("ports").

The application provides real implementations (adapters): a local folder
and SQLite today, cloud storage or a search service later. Every call
carries the tenant, so an adapter can never be asked for another
workspace's data by accident.
"""
from typing import Protocol, Sequence

from .models import Passage
from .records import DocumentRecord, VersionRecord


class FileStore(Protocol):
    def put(self, tenant_id: str, key: str, data: bytes) -> None: ...
    def get(self, tenant_id: str, key: str) -> bytes: ...
    def delete(self, tenant_id: str, key: str) -> None:
        """Remove the file; removing a missing file is not an error."""


class PassageIndex(Protocol):
    def replace(self, tenant_id: str, document_id: str, version: int,
                passages: Sequence[Passage]) -> None:
        """
        Make exactly these passages searchable for the document, in one step:
        passages of every other version disappear at the same moment.
        Calling it twice with the same input gives the same result.
        """

    def remove(self, tenant_id: str, document_id: str) -> None:
        """No passage of the document stays searchable."""

    def count(self, tenant_id: str, document_id: str) -> int: ...


class DocumentCatalog(Protocol):
    def add(self, document: DocumentRecord, version: VersionRecord) -> None: ...
    def save_document(self, document: DocumentRecord) -> None: ...
    def save_version(self, version: VersionRecord) -> None: ...

    def document(self, tenant_id: str, document_id: str) -> DocumentRecord | None: ...
    def documents(self, tenant_id: str) -> list[DocumentRecord]:
        """Documents that are not deleted, newest first."""
    def by_filename(self, tenant_id: str, filename: str) -> DocumentRecord | None:
        """The document (not deleted) with this file name, ignoring letter case."""
    def by_content(self, tenant_id: str, sha256: str) -> VersionRecord | None:
        """A version with this content that is waiting, processing or ready, in a document that is not deleted."""

    def version(self, tenant_id: str, document_id: str, version: int) -> VersionRecord | None: ...
    def versions(self, tenant_id: str, document_id: str) -> list[VersionRecord]: ...

    def job(self, job_id: str) -> VersionRecord | None:
        """For the background worker only; job IDs are never accepted from callers."""
    def unfinished_jobs(self) -> list[VersionRecord]:
        """Jobs still queued or processing (e.g. after a restart)."""
