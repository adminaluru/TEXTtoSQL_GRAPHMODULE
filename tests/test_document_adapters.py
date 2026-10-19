"""A8.2: local folder, SQLite catalog/index, worker and isolated reader."""
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hybrid_contracts import AccessContext, DocumentJob, DocumentState

from adapters.document_files import LocalFolderFileStore
from adapters.document_sqlite import SqliteDatabase, SqliteDocumentCatalog, SqlitePassageIndex
from adapters.document_worker import DocumentWorker
from adapters.isolated_extractor import TOO_SLOW, IsolatedExtractor
from document_ingestion import (DocumentIngestionService, DocumentRecord, DocumentRejected, IngestionLimits,
                                Location, Passage, VersionRecord, extract_document)
from tests import document_child_targets
from tests.document_test_files import make_docx, make_pdf

ACME = AccessContext(subject_id="alice", tenant_id="tenant-acme", policy_version="test")
NOVA = AccessContext(subject_id="bob", tenant_id="tenant-nova", policy_version="test")
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path):
    db = SqliteDatabase(tmp_path / "catalog.sqlite3")
    yield db
    db.close()


def record(tenant="tenant-acme", document_id="doc_1", version=1, sha="a" * 64, state=DocumentState.QUEUED,
           filename="Terms.pdf"):
    document = DocumentRecord(document_id, tenant, filename, "alice", NOW, latest_version=version)
    job = DocumentJob(job_id=f"job_{document_id}_{version}", tenant_id=tenant, source_id=document_id,
                      source_version=str(version), state=state, created_at=NOW, updated_at=NOW,
                      progress_percent=100 if state == DocumentState.READY else 0,
                      message="m" if state == DocumentState.FAILED else None)
    version_record = VersionRecord(document_id, version, sha, 10, "pdf", f"{document_id}/v{version}.pdf",
                                   "alice", job, notes=("1 hidden row(s) were skipped.",), passage_count=2)
    return document, version_record


# ==========================================================
# Local folder
# ==========================================================

def test_files_are_stored_per_workspace_and_removed_cleanly(tmp_path):
    store = LocalFolderFileStore(tmp_path)
    store.put("tenant-acme", "doc_1/v1.pdf", b"acme")
    store.put("tenant-nova", "doc_1/v1.pdf", b"nova")
    assert store.get("tenant-acme", "doc_1/v1.pdf") == b"acme"
    assert (tmp_path / "tenant-acme" / "doc_1" / "v1.pdf").read_bytes() == b"acme"
    store.delete("tenant-acme", "doc_1/v1.pdf")
    store.delete("tenant-acme", "doc_1/v1.pdf")          # deleting twice is fine
    assert not (tmp_path / "tenant-acme" / "doc_1").exists()
    assert store.get("tenant-nova", "doc_1/v1.pdf") == b"nova"
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("key", ["../x/v1.pdf", "doc_1/../../v1.pdf", "C:/doc/v1.pdf", "doc_1/v1.exe",
                                 "doc_1/terms.pdf", "/doc_1/v1.pdf"])
def test_storage_keys_cannot_escape_the_folder(tmp_path, key):
    with pytest.raises(ValueError, match="Invalid storage key"):
        LocalFolderFileStore(tmp_path).put("tenant-acme", key, b"x")


def test_unusual_tenant_ids_get_a_fixed_folder_name(tmp_path):
    store = LocalFolderFileStore(tmp_path)
    store.put("../../etc", "doc_1/v1.pdf", b"x")
    folders = [p.name for p in tmp_path.iterdir()]
    assert len(folders) == 1 and folders[0].startswith("t_") and len(folders[0]) == 34


# ==========================================================
# SQLite catalog and passage index
# ==========================================================

def test_catalog_round_trip_and_tenant_filter(database):
    catalog = SqliteDocumentCatalog(database)
    document, version = record()
    catalog.add(document, version)
    assert catalog.document("tenant-acme", "doc_1") == document
    assert catalog.version("tenant-acme", "doc_1", 1) == version
    assert catalog.job(version.job.job_id) == version
    assert catalog.by_filename("tenant-acme", "terms.PDF") == document
    assert catalog.by_content("tenant-acme", "a" * 64) == version
    # Another workspace sees nothing, even with the right IDs.
    assert catalog.document("tenant-nova", "doc_1") is None
    assert catalog.version("tenant-nova", "doc_1", 1) is None
    assert catalog.versions("tenant-nova", "doc_1") == []
    assert catalog.by_filename("tenant-nova", "Terms.pdf") is None
    assert catalog.by_content("tenant-nova", "a" * 64) is None
    assert catalog.documents("tenant-nova") == []


def test_catalog_cannot_move_a_document_to_another_workspace(database):
    catalog = SqliteDocumentCatalog(database)
    document, version = record()
    catalog.add(document, version)
    catalog.save_document(document.with_(tenant_id="tenant-nova", latest_version=9))
    assert catalog.document("tenant-acme", "doc_1").latest_version == 1
    assert catalog.document("tenant-nova", "doc_1") is None


def test_catalog_duplicates_ignore_failed_deleted_and_deleted_documents(database):
    catalog = SqliteDocumentCatalog(database)
    catalog.add(*record(document_id="doc_f", sha="f" * 64, state=DocumentState.FAILED))
    assert catalog.by_content("tenant-acme", "f" * 64) is None
    document, version = record(document_id="doc_d", sha="d" * 64, state=DocumentState.READY, filename="Gone.pdf")
    catalog.add(document.with_(deleted=True), version)
    assert catalog.by_content("tenant-acme", "d" * 64) is None
    assert catalog.by_filename("tenant-acme", "Gone.pdf") is None


def test_catalog_lists_unfinished_jobs(database):
    catalog = SqliteDocumentCatalog(database)
    for i, state in enumerate([DocumentState.QUEUED, DocumentState.READY, DocumentState.FAILED]):
        catalog.add(*record(document_id=f"doc_{i}", sha=str(i) * 64, state=state))
    assert [v.document_id for v in catalog.unfinished_jobs()] == ["doc_0"]


def passages(*texts):
    return [Passage(i, text, Location("page", i, i)) for i, text in enumerate(texts, start=1)]


def test_index_replace_is_complete_and_repeatable(database):
    index = SqlitePassageIndex(database)
    index.replace("tenant-acme", "doc_1", 1, passages("one", "two", "three"))
    index.replace("tenant-acme", "doc_1", 2, passages("new"))
    index.replace("tenant-acme", "doc_1", 2, passages("new"))
    index.replace("tenant-nova", "doc_1", 1, passages("nova"))
    assert index.count("tenant-acme", "doc_1") == 1
    index.remove("tenant-acme", "doc_1")
    assert index.count("tenant-acme", "doc_1") == 0
    assert index.count("tenant-nova", "doc_1") == 1


def test_index_supports_full_text_search(database):
    SqlitePassageIndex(database).replace("tenant-acme", "doc_1", 1, passages("Late payment fee is 2%", "Other"))
    rows = database.query("SELECT text FROM passages WHERE passages MATCH ? AND tenant_id = ?",
                          ("payment", "tenant-acme"))
    assert [r["text"] for r in rows] == ["Late payment fee is 2%"]


def test_service_lifecycle_on_sqlite_and_local_folder(database, tmp_path):
    queue = []
    service = DocumentIngestionService(SqliteDocumentCatalog(database), LocalFolderFileStore(tmp_path / "files"),
                                       SqlitePassageIndex(database), enqueue=queue.append)
    index = SqlitePassageIndex(database)

    def run():
        while queue:
            service.process(queue.pop(0))

    document_id = service.upload(ACME, "terms.pdf", make_pdf(["Net 30"])).document.document_id
    run()
    assert index.count("tenant-acme", document_id) == 1
    service.upload(ACME, "terms.pdf", make_pdf(["Net 45", "Page two"]))
    run()
    document, versions = service.document(ACME, document_id)
    assert document.active_version == 2 and index.count("tenant-acme", document_id) == 2
    assert [v.job.state for v in versions] == [DocumentState.DELETED, DocumentState.READY]
    assert service.upload(ACME, "copy.pdf", make_pdf(["Net 45", "Page two"])).duplicate
    assert service.documents(NOVA) == []

    service.delete(ACME, document_id)
    assert index.count("tenant-acme", document_id) == 0
    assert not [p for p in (tmp_path / "files").rglob("*") if p.is_file()]


# ==========================================================
# Background worker
# ==========================================================

def test_worker_processes_jobs_in_order_and_survives_errors():
    seen = []

    def process(job_id):
        seen.append(job_id)
        if job_id == "bad":
            raise RuntimeError("boom")

    worker = DocumentWorker()
    worker.start(process)
    for job_id in ("a", "bad", "c"):
        worker.enqueue(job_id)
    worker.wait_until_idle()
    worker.stop()
    assert seen == ["a", "bad", "c"]


def test_worker_runs_in_the_background():
    started = threading.Event()
    release = threading.Event()

    def process(job_id):
        started.set()
        release.wait(5)

    worker = DocumentWorker()
    worker.start(process)
    worker.enqueue("slow")
    assert started.wait(5)        # enqueue returned while the job is still running
    release.set()
    worker.wait_until_idle()
    worker.stop()


# ==========================================================
# Isolated reader process
# ==========================================================

def test_isolated_reader_returns_the_same_result_as_in_process():
    data = make_docx(lambda d: d.add_paragraph("Payment terms"))
    isolated = IsolatedExtractor(timeout_seconds=60)(data, "a.docx", IngestionLimits())
    assert isolated == extract_document(data, "a.docx")


def test_isolated_reader_passes_refusals_through():
    with pytest.raises(DocumentRejected) as caught:
        IsolatedExtractor(timeout_seconds=60)(make_pdf([None]), "scan.pdf", IngestionLimits())
    assert caught.value.code == "no_readable_text"


def test_a_reader_that_hangs_is_stopped_and_the_file_refused():
    extractor = IsolatedExtractor(timeout_seconds=3, target=document_child_targets.hang)
    with pytest.raises(DocumentRejected) as caught:
        extractor(b"%PDF-", "a.pdf", IngestionLimits())
    assert caught.value.code == "too_complex" and caught.value.message == TOO_SLOW


def test_a_reader_that_crashes_is_a_technical_failure():
    extractor = IsolatedExtractor(timeout_seconds=60, target=document_child_targets.crash)
    with pytest.raises(RuntimeError, match="ended unexpectedly"):
        extractor(b"%PDF-", "a.pdf", IngestionLimits())


def test_timeout_must_be_positive():
    with pytest.raises(ValueError):
        IsolatedExtractor(timeout_seconds=0)
