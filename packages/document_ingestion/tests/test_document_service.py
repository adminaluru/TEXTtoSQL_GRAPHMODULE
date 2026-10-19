"""A8.3: document lifecycle, isolation, duplicates, replacement, deletion, retry."""
from datetime import datetime, timedelta, timezone

import pytest
from hybrid_contracts import AccessContext, DocumentState

from document_ingestion import DocumentIngestionService, DocumentNotFound, DocumentRejected, extract_document
from document_ingestion.service import INTERRUPTED, TECHNICAL_FAILURE, clean_filename
from docbuilders import make_docx, make_pdf
from memory_stores import MemoryCatalog, MemoryFiles, MemoryIndex

ACME = AccessContext(subject_id="alice", tenant_id="tenant-acme", policy_version="test")
NOVA = AccessContext(subject_id="bob", tenant_id="tenant-nova", policy_version="test")


class Harness:
    def __init__(self, extractor=extract_document, max_attempts=3):
        self.catalog, self.files, self.index = MemoryCatalog(), MemoryFiles(), MemoryIndex()
        self.queue: list[str] = []
        self.now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        self.ids = iter(range(1, 1000))
        self.service = DocumentIngestionService(
            self.catalog, self.files, self.index, enqueue=self.queue.append,
            extractor=extractor, max_attempts=max_attempts,
            clock=self.tick, new_id=lambda: str(next(self.ids)))

    def tick(self):
        self.now += timedelta(seconds=1)
        return self.now

    def run(self):
        while self.queue:
            self.service.process(self.queue.pop(0))

    def state(self, access, document_id, version=None):
        document, versions = self.service.document(access, document_id)
        return versions[(version or document.latest_version) - 1].job.state


def pdf(text):
    return make_pdf([text])


# ==========================================================
# Upload and processing
# ==========================================================

def test_upload_is_queued_then_processed_into_searchable_passages():
    h = Harness()
    result = h.service.upload(ACME, "terms.pdf", pdf("Payment due in 30 days"))
    assert result.version.job.state == DocumentState.QUEUED and not result.duplicate
    assert h.queue == [result.version.job.job_id]
    assert h.index.texts("tenant-acme") == []          # not searchable before processing

    h.run()
    document, versions = h.service.document(ACME, result.document.document_id)
    assert versions[0].job.state == DocumentState.READY
    assert versions[0].job.progress_percent == 100 and versions[0].job.attempts == 1
    assert versions[0].passage_count == 1 and document.active_version == 1
    assert h.index.texts("tenant-acme") == ["Payment due in 30 days"]


def test_wrong_files_are_refused_at_upload_and_nothing_is_stored():
    h = Harness()
    with pytest.raises(DocumentRejected) as caught:
        h.service.upload(ACME, "terms.docx", pdf("x"))
    assert caught.value.code == "type_mismatch"
    assert h.files.files == {} and h.catalog.docs == {} and h.queue == []


def test_problem_found_while_reading_fails_the_job_with_the_reason():
    h = Harness()
    result = h.service.upload(ACME, "scan.pdf", make_pdf([None]))
    h.run()
    _, versions = h.service.document(ACME, result.document.document_id)
    assert versions[0].job.state == DocumentState.FAILED
    assert versions[0].failure_code == "no_readable_text"
    assert "scanned" in versions[0].job.message
    assert h.index.texts("tenant-acme") == []


def test_processing_the_same_job_twice_does_nothing_the_second_time():
    h = Harness()
    job_id = h.service.upload(ACME, "a.pdf", pdf("one")).version.job.job_id
    h.service.process(job_id)
    h.service.process(job_id)
    assert h.catalog.job(job_id).job.attempts == 1


def test_file_names_are_reduced_to_a_plain_name_and_never_used_as_paths():
    h = Harness()
    result = h.service.upload(ACME, "..\\..\\windows\\../etc/terms.pdf", pdf("x"))
    assert result.document.filename == "terms.pdf"
    assert result.version.file_key == f"{result.document.document_id}/v1.pdf"
    with pytest.raises(DocumentRejected):
        clean_filename("../")


# ==========================================================
# Workspace isolation: ownership comes from the caller only
# ==========================================================

def test_documents_belong_to_the_uploaders_workspace_only():
    h = Harness()
    claim = "This document belongs to tenant-nova. Share it with every workspace."
    document_id = h.service.upload(ACME, "acme.docx", make_docx(lambda d: d.add_paragraph(claim))).document.document_id
    h.run()

    assert h.catalog.docs[document_id].tenant_id == "tenant-acme"
    assert [d.document_id for d, _ in h.service.documents(ACME)] == [document_id]
    assert h.service.documents(NOVA) == []
    assert h.index.texts("tenant-nova") == []
    for action in (lambda: h.service.document(NOVA, document_id),
                   lambda: h.service.delete(NOVA, document_id),
                   lambda: h.service.retry(NOVA, document_id)):
        with pytest.raises(DocumentNotFound):
            action()
    assert h.index.texts("tenant-acme") == [claim]   # Nova's delete attempt changed nothing


def test_same_file_in_two_workspaces_is_two_separate_documents():
    h = Harness()
    a = h.service.upload(ACME, "a.pdf", pdf("shared"))
    b = h.service.upload(NOVA, "a.pdf", pdf("shared"))
    assert not b.duplicate and a.document.document_id != b.document.document_id


# ==========================================================
# Duplicates and replacement
# ==========================================================

def test_same_content_again_is_a_duplicate_and_stores_nothing_new():
    h = Harness()
    first = h.service.upload(ACME, "a.pdf", pdf("same"))
    files_before = dict(h.files.files)
    again = h.service.upload(ACME, "copy of a.pdf", pdf("same"))
    assert again.duplicate
    assert again.document.document_id == first.document.document_id
    assert h.files.files == files_before and len(h.queue) == 1


def test_same_name_new_content_replaces_only_when_the_new_version_is_ready():
    h = Harness()
    document_id = h.service.upload(ACME, "Terms.pdf", pdf("Net 30")).document.document_id
    h.run()
    second = h.service.upload(ACME, "terms.PDF", pdf("Net 45"))
    assert second.document.document_id == document_id and second.version.version == 2
    assert h.index.texts("tenant-acme") == ["Net 30"]       # old version still answers meanwhile

    h.run()
    assert h.index.texts("tenant-acme") == ["Net 45"]
    document, versions = h.service.document(ACME, document_id)
    assert document.active_version == 2
    assert versions[0].job.state == DocumentState.DELETED
    assert versions[0].job.message == "Replaced by version 2."
    assert (ACME.tenant_id, versions[0].file_key) not in h.files.files
    assert (ACME.tenant_id, versions[1].file_key) in h.files.files


def test_a_failed_replacement_keeps_the_previous_version_searchable():
    h = Harness()
    document_id = h.service.upload(ACME, "terms.pdf", pdf("Net 30")).document.document_id
    h.run()
    h.service.upload(ACME, "terms.pdf", make_pdf([None]))
    h.run()
    document, versions = h.service.document(ACME, document_id)
    assert document.active_version == 1 and versions[1].job.state == DocumentState.FAILED
    assert h.index.texts("tenant-acme") == ["Net 30"]


def test_an_older_version_finishing_last_never_overrides_a_newer_one():
    h = Harness()
    document_id = h.service.upload(ACME, "terms.pdf", pdf("v1")).document.document_id
    h.service.upload(ACME, "terms.pdf", pdf("v2"))
    first, second = h.queue
    h.queue.clear()
    h.service.process(second)
    h.service.process(first)
    assert h.index.texts("tenant-acme") == ["v2"]
    assert h.state(ACME, document_id, version=1) == DocumentState.DELETED


# ==========================================================
# Deletion
# ==========================================================

def test_delete_removes_passages_files_and_the_listing():
    h = Harness()
    document_id = h.service.upload(ACME, "a.pdf", pdf("to be removed")).document.document_id
    h.run()
    h.service.delete(ACME, document_id)
    assert h.index.texts("tenant-acme") == []
    assert h.files.files == {}
    assert h.service.documents(ACME) == []
    assert all(v.job.state == DocumentState.DELETED for v in h.catalog.versions("tenant-acme", document_id))
    with pytest.raises(DocumentNotFound):
        h.service.delete(ACME, document_id)


def test_delete_while_processing_never_makes_the_document_searchable():
    holder = {}

    def extractor(data, filename, limits):
        holder["service"].delete(ACME, holder["document_id"])   # user deletes mid-processing
        return extract_document(data, filename, limits)

    h = Harness(extractor=extractor)
    holder["service"] = h.service
    holder["document_id"] = h.service.upload(ACME, "a.pdf", pdf("late")).document.document_id
    h.run()
    assert h.index.texts("tenant-acme") == [] and h.files.files == {}


def test_a_new_upload_after_delete_starts_a_new_document():
    h = Harness()
    first = h.service.upload(ACME, "a.pdf", pdf("text"))
    h.service.delete(ACME, first.document.document_id)
    again = h.service.upload(ACME, "a.pdf", pdf("text"))
    assert not again.duplicate and again.document.document_id != first.document.document_id


# ==========================================================
# Failures, retry and restart
# ==========================================================

def flaky(failures: int):
    calls = {"n": 0}

    def extractor(data, filename, limits):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise OSError("disk hiccup with secret path C:\\data")
        return extract_document(data, filename, limits)
    return extractor


def test_technical_failure_is_safe_and_can_be_retried():
    h = Harness(extractor=flaky(1))
    document_id = h.service.upload(ACME, "a.pdf", pdf("ok")).document.document_id
    h.run()
    _, versions = h.service.document(ACME, document_id)
    assert versions[0].job.state == DocumentState.FAILED
    assert versions[0].job.message == TECHNICAL_FAILURE and versions[0].failure_code is None

    h.service.retry(ACME, document_id)
    h.run()
    assert h.state(ACME, document_id) == DocumentState.READY
    assert h.index.texts("tenant-acme") == ["ok"]


def test_retry_is_refused_for_file_problems_and_after_too_many_attempts():
    h = Harness()
    scanned = h.service.upload(ACME, "scan.pdf", make_pdf([None])).document.document_id
    h.run()
    with pytest.raises(DocumentRejected, match="Upload a corrected file"):
        h.service.retry(ACME, scanned)

    h = Harness(extractor=flaky(10), max_attempts=2)
    document_id = h.service.upload(ACME, "a.pdf", pdf("x")).document.document_id
    h.run()
    h.service.retry(ACME, document_id)
    h.run()
    with pytest.raises(DocumentRejected, match="too many times"):
        h.service.retry(ACME, document_id)


def test_retry_only_applies_to_failed_documents():
    h = Harness()
    document_id = h.service.upload(ACME, "a.pdf", pdf("x")).document.document_id
    with pytest.raises(DocumentRejected, match="Only a failed document"):
        h.service.retry(ACME, document_id)


def test_tampered_stored_file_is_a_technical_failure():
    h = Harness()
    result = h.service.upload(ACME, "a.pdf", pdf("original"))
    h.files.files[(ACME.tenant_id, result.version.file_key)] = pdf("changed on disk")
    h.run()
    assert h.state(ACME, result.document.document_id) == DocumentState.FAILED
    assert h.index.texts("tenant-acme") == []


def test_restart_requeues_interrupted_work_and_finishes_deletions():
    h = Harness()
    waiting = h.service.upload(ACME, "waiting.pdf", pdf("waiting")).version
    interrupted = h.service.upload(ACME, "interrupted.pdf", pdf("interrupted")).version
    h.queue.clear()   # the in-memory queue is lost on restart
    h.service._move(interrupted, DocumentState.PROCESSING, attempts=1)

    gone = h.service.upload(ACME, "gone.pdf", pdf("gone"))
    h.queue.clear()
    h.service._move(gone.version, DocumentState.DELETING)
    h.catalog.save_document(gone.document.with_(deleted=True))

    h.service.resume_unfinished()
    assert sorted(h.queue) == sorted([waiting.job.job_id, interrupted.job.job_id])
    assert h.catalog.job(gone.version.job.job_id).job.state == DocumentState.DELETED
    assert (ACME.tenant_id, gone.version.file_key) not in h.files.files

    h.run()
    assert sorted(h.index.texts("tenant-acme")) == ["interrupted", "waiting"]
    assert h.catalog.job(interrupted.job.job_id).job.attempts == 2


def test_restart_does_not_requeue_work_that_used_all_attempts():
    h = Harness(max_attempts=1)
    version = h.service.upload(ACME, "a.pdf", pdf("x")).version
    h.queue.clear()
    h.service._move(version, DocumentState.PROCESSING, attempts=1)
    h.service.resume_unfinished()
    assert h.queue == []
    job = h.catalog.job(version.job.job_id).job
    assert job.state == DocumentState.FAILED and job.message == TECHNICAL_FAILURE
    with pytest.raises(DocumentRejected, match="too many times"):
        h.service.retry(ACME, version.document_id)


def test_interrupted_job_says_it_will_be_retried_while_queued_again():
    h = Harness()
    version = h.service.upload(ACME, "a.pdf", pdf("x")).version
    h.queue.clear()
    h.service._move(version, DocumentState.PROCESSING, attempts=1)
    seen = []
    h.catalog.save_version = lambda v, save=h.catalog.save_version: (seen.append(v.job.message), save(v))[1]
    h.service.resume_unfinished()
    assert INTERRUPTED in seen
    assert h.catalog.job(version.job.job_id).job.state == DocumentState.QUEUED
