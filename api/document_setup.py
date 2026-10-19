"""Build the document service from settings (composition for Step 8).

Choose the storage here; nothing else knows which adapters are in use.
Today: files in a local folder, catalog and passages in a local SQLite
file, each file read in a separate process with a time limit.
"""
from pathlib import Path
from typing import Callable, NamedTuple

from adapters.document_files import LocalFolderFileStore
from adapters.document_search import SqliteDocumentDirectory, SqlitePassageSearch
from adapters.document_sqlite import SqliteDatabase, SqliteDocumentCatalog, SqlitePassageIndex
from adapters.document_worker import DocumentWorker
from adapters.isolated_extractor import IsolatedExtractor
from document_ingestion import DocumentIngestionService, IngestionLimits
from document_retrieval import DocumentRetriever


class DocumentParts(NamedTuple):
    service: DocumentIngestionService
    worker: DocumentWorker
    retriever: DocumentRetriever
    # True when the workspace has at least one searchable document (Step 11).
    has_documents: Callable[[str], bool]


def document_service_from(settings) -> DocumentParts:
    if settings.document_max_file_mb <= 0:
        raise ValueError("DOCUMENT_MAX_FILE_MB must be positive.")
    root = Path(settings.document_storage_dir)
    database = SqliteDatabase(root / "catalog.sqlite3")
    worker = DocumentWorker()
    service = DocumentIngestionService(
        catalog=SqliteDocumentCatalog(database),
        files=LocalFolderFileStore(root / "files"),
        index=SqlitePassageIndex(database),
        enqueue=worker.enqueue,
        extractor=IsolatedExtractor(settings.document_processing_timeout_seconds),
        limits=IngestionLimits(max_file_bytes=settings.document_max_file_mb * 1024 * 1024),
    )
    # Step 9: search reads the same index the service writes.
    directory = SqliteDocumentDirectory(database)
    retriever = DocumentRetriever(SqlitePassageSearch(database), directory)
    return DocumentParts(service, worker, retriever, directory.has_searchable)


def start_document_processing(service: DocumentIngestionService, worker: DocumentWorker) -> None:
    worker.start(service.process)
    # Work interrupted by the last shutdown is picked up again.
    service.resume_unfinished()
