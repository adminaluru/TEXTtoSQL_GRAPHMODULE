"""Document lifecycle: upload, background processing, replacement, deletion.

    upload ──> queued ──> processing ──> ready      (passages searchable)
                              └────────> failed     (reason shown; retry if technical)
    delete ──> deleting ──> deleted                 (passages removed first)

Rules
- The workspace (tenant) comes from the verified caller, never from the file.
- The same content uploaded again in a workspace is a duplicate: nothing new is stored.
- The same file name with new content is a new version. Its passages replace
  the old ones only when it is ready; until then the old version stays searchable.
- Deleting removes the passages first, then the files.
- Another workspace's document is reported as "not found", never as "forbidden",
  so its existence is not revealed.
"""
import hashlib
import logging
import secrets
import threading
from datetime import datetime, timezone
from typing import Callable

from hybrid_contracts import AccessContext, DocumentJob, DocumentState

from .detection import detect_file_type
from .errors import DocumentRejected
from .extraction import extract_document
from .limits import IngestionLimits
from .models import ExtractedDocument
from .ports import DocumentCatalog, FileStore, PassageIndex
from .records import DocumentRecord, UploadResult, VersionRecord
from .text import clean_text

logger = logging.getLogger(__name__)

TECHNICAL_FAILURE = "Processing failed for a technical reason. It can be retried."
INTERRUPTED = "Processing was interrupted and will be retried."
NOT_FOUND = "Document not found."

Extractor = Callable[[bytes, str, IngestionLimits], ExtractedDocument]


class DocumentNotFound(LookupError):
    """Missing, deleted, or in another workspace (deliberately indistinguishable)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentIngestionService:
    def __init__(
        self,
        catalog: DocumentCatalog,
        files: FileStore,
        index: PassageIndex,
        enqueue: Callable[[str], None],
        extractor: Extractor = extract_document,
        limits: IngestionLimits = IngestionLimits(),
        max_attempts: int = 3,
        clock: Callable[[], datetime] = _utcnow,
        new_id: Callable[[], str] = lambda: secrets.token_urlsafe(16),
    ):
        self._catalog = catalog
        self._files = files
        self._index = index
        self._enqueue = enqueue
        self._extractor = extractor
        self._limits = limits
        self._max_attempts = max_attempts
        self._clock = clock
        self._new_id = new_id
        # Status changes from request threads and the worker never interleave.
        self._lock = threading.RLock()

    # ======================================================
    # Upload
    # ======================================================

    def upload(self, access: AccessContext, filename: str, data: bytes) -> UploadResult:
        filename = clean_filename(filename)
        # Fast checks now, so the uploader hears about a wrong file at once.
        file_type = detect_file_type(data, filename, self._limits)
        sha256 = hashlib.sha256(data).hexdigest()
        tenant = access.tenant_id

        with self._lock:
            same_content = self._catalog.by_content(tenant, sha256)
            if same_content is not None:
                document = self._catalog.document(tenant, same_content.document_id)
                return UploadResult(document, same_content, duplicate=True)

            now = self._clock()
            document = self._catalog.by_filename(tenant, filename)
            if document is None:
                document = DocumentRecord(
                    document_id=f"doc_{self._new_id()}", tenant_id=tenant, filename=filename,
                    uploaded_by=access.subject_id, created_at=now, latest_version=1)
            else:
                document = document.with_(latest_version=document.latest_version + 1)

            number = document.latest_version
            version = VersionRecord(
                document_id=document.document_id,
                version=number,
                sha256=sha256,
                size_bytes=len(data),
                file_type=file_type,
                file_key=f"{document.document_id}/v{number}.{file_type}",
                uploaded_by=access.subject_id,
                job=DocumentJob(
                    job_id=f"job_{self._new_id()}", tenant_id=tenant,
                    source_id=document.document_id, source_version=str(number),
                    state=DocumentState.QUEUED, created_at=now, updated_at=now),
            )
            # File first: a job never points at a file that is not there.
            self._files.put(tenant, version.file_key, data)
            self._catalog.add(document, version)

        self._enqueue(version.job.job_id)
        return UploadResult(document, version)

    # ======================================================
    # Background processing (called by the worker)
    # ======================================================

    def process(self, job_id: str) -> None:
        with self._lock:
            version = self._catalog.job(job_id)
            if version is None or version.job.state != DocumentState.QUEUED:
                return  # already processed, deleted, or unknown
            version = self._move(version, DocumentState.PROCESSING, progress_percent=10,
                                 attempts=version.job.attempts + 1)
            document = self._catalog.document(version.job.tenant_id, version.document_id)

        try:
            data = self._files.get(document.tenant_id, version.file_key)
            if hashlib.sha256(data).hexdigest() != version.sha256:
                raise RuntimeError("stored file does not match its upload")
            extracted = self._extractor(data, document.filename, self._limits)
        except DocumentRejected as refusal:
            with self._lock:
                self._fail(version.job.job_id, refusal.message, refusal.code)
            return
        except Exception as exc:
            logger.warning("document_processing_failed job_id=%s error=%s", job_id, type(exc).__name__)
            with self._lock:
                self._fail(version.job.job_id, TECHNICAL_FAILURE, None)
            return

        with self._lock:
            self._finish(version.job.job_id, extracted)

    def _finish(self, job_id: str, extracted: ExtractedDocument) -> None:
        version = self._catalog.job(job_id)
        if version is None or version.job.state != DocumentState.PROCESSING:
            return  # deleted while it was being read
        tenant = version.job.tenant_id
        document = self._catalog.document(tenant, version.document_id)
        if document is None or document.deleted:
            return
        if document.latest_version != version.version:
            # A newer upload of the same file name arrived meanwhile; it wins.
            self._retire(version, f"Replaced by version {document.latest_version}.")
            return

        self._index.replace(tenant, document.document_id, version.version, extracted.passages)
        previous = document.active_version
        self._catalog.save_document(document.with_(active_version=version.version))
        self._move(version, DocumentState.READY, progress_percent=100,
                   notes=extracted.notes, passage_count=len(extracted.passages))

        if previous is not None and previous != version.version:
            old = self._catalog.version(tenant, document.document_id, previous)
            if old is not None:
                self._retire(old, f"Replaced by version {version.version}.")

    def _fail(self, job_id: str, message: str, code: str | None) -> None:
        version = self._catalog.job(job_id)
        if version is None or version.job.state != DocumentState.PROCESSING:
            return
        self._move(version, DocumentState.FAILED, message=message, failure_code=code)

    # ======================================================
    # Retry and restart recovery
    # ======================================================

    def can_retry(self, version: VersionRecord) -> bool:
        return (version.job.state == DocumentState.FAILED and version.failure_code is None
                and version.job.attempts < self._max_attempts)

    def retry(self, access: AccessContext, document_id: str) -> VersionRecord:
        """Queue the latest version again after a technical failure."""
        with self._lock:
            document = self._owned(access, document_id)
            version = self._catalog.version(access.tenant_id, document_id, document.latest_version)
            if version.job.state != DocumentState.FAILED:
                raise DocumentRejected("not_retryable", "Only a failed document can be retried.")
            if version.failure_code is not None:
                raise DocumentRejected(
                    "not_retryable", "This file cannot be processed as it is. Upload a corrected file.")
            if version.job.attempts >= self._max_attempts:
                raise DocumentRejected(
                    "not_retryable", "This file failed too many times. Upload it again later.")
            version = self._move(version, DocumentState.QUEUED, message=None)
        self._enqueue(version.job.job_id)
        return version

    def resume_unfinished(self) -> None:
        """At start-up: requeue interrupted work and finish interrupted deletions."""
        queued = []
        with self._lock:
            for version in self._catalog.unfinished_jobs():
                state = version.job.state
                if state == DocumentState.DELETING:
                    self._complete_deletion(version)
                elif state == DocumentState.PROCESSING:
                    version = self._move(version, DocumentState.FAILED, message=INTERRUPTED)
                    if version.job.attempts < self._max_attempts:
                        queued.append(self._move(version, DocumentState.QUEUED, message=None))
                    else:
                        self._move_message(version, TECHNICAL_FAILURE)
                elif state == DocumentState.QUEUED:
                    queued.append(version)
        for version in queued:
            self._enqueue(version.job.job_id)

    # ======================================================
    # Delete and read
    # ======================================================

    def delete(self, access: AccessContext, document_id: str) -> None:
        with self._lock:
            document = self._owned(access, document_id)
            # Marked first, so no running job can make it searchable again.
            self._catalog.save_document(document.with_(deleted=True, active_version=None))
            self._index.remove(access.tenant_id, document_id)
            for version in self._catalog.versions(access.tenant_id, document_id):
                if version.job.state not in (DocumentState.DELETING, DocumentState.DELETED):
                    version = self._move(version, DocumentState.DELETING)
                self._complete_deletion(version)

    def documents(self, access: AccessContext) -> list[tuple[DocumentRecord, VersionRecord]]:
        """Each document with its latest version (the one whose status matters)."""
        with self._lock:
            return [
                (document, self._catalog.version(access.tenant_id, document.document_id, document.latest_version))
                for document in self._catalog.documents(access.tenant_id)
            ]

    def document(self, access: AccessContext, document_id: str) -> tuple[DocumentRecord, list[VersionRecord]]:
        with self._lock:
            document = self._owned(access, document_id)
            return document, self._catalog.versions(access.tenant_id, document_id)

    # ======================================================
    # Helpers
    # ======================================================

    def _owned(self, access: AccessContext, document_id: str) -> DocumentRecord:
        document = self._catalog.document(access.tenant_id, document_id)
        if document is None or document.deleted:
            raise DocumentNotFound(NOT_FOUND)
        return document

    def _retire(self, version: VersionRecord, message: str) -> None:
        if version.job.state in (DocumentState.DELETING, DocumentState.DELETED):
            return
        version = self._move(version, DocumentState.DELETING)
        self._complete_deletion(version, message)

    def _complete_deletion(self, version: VersionRecord, message: str | None = None) -> None:
        if version.job.state != DocumentState.DELETING:
            return
        tenant = version.job.tenant_id
        document = self._catalog.document(tenant, version.document_id)
        if document is not None and document.deleted:
            self._index.remove(tenant, version.document_id)
        self._files.delete(tenant, version.file_key)
        self._move(version, DocumentState.DELETED, message=message)

    def _move(self, version: VersionRecord, state: DocumentState, **changes) -> VersionRecord:
        if not version.job.can_transition_to(state):
            raise RuntimeError(f"Invalid document state change {version.job.state} -> {state}")
        job_fields = {k: changes.pop(k) for k in ("progress_percent", "attempts", "message") if k in changes}
        job = DocumentJob.model_validate({
            **version.job.model_dump(), **job_fields, "state": state, "updated_at": self._clock()})
        version = version.with_(job=job, **changes)
        self._catalog.save_version(version)
        return version

    def _move_message(self, version: VersionRecord, message: str) -> VersionRecord:
        job = DocumentJob.model_validate({**version.job.model_dump(), "message": message,
                                          "updated_at": self._clock()})
        version = version.with_(job=job)
        self._catalog.save_version(version)
        return version


def clean_filename(filename: str) -> str:
    """Keep only the last part of a path, without control characters."""
    name = clean_text((filename or "").replace("\\", "/").rsplit("/", 1)[-1]).replace("\n", " ")
    if not name or name in (".", "..") or len(name) > 255:
        raise DocumentRejected("unsupported_type", "The file name is not valid.")
    return name
