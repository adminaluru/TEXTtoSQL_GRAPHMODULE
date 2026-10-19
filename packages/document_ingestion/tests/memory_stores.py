"""Simple in-memory versions of the storage ports, for tests."""
from hybrid_contracts import DocumentState

from document_ingestion import DocumentRecord, VersionRecord

ACTIVE = (DocumentState.QUEUED, DocumentState.PROCESSING, DocumentState.READY)
UNFINISHED = (DocumentState.QUEUED, DocumentState.PROCESSING, DocumentState.DELETING)


class MemoryFiles:
    def __init__(self):
        self.files: dict[tuple[str, str], bytes] = {}

    def put(self, tenant_id, key, data):
        self.files[(tenant_id, key)] = data

    def get(self, tenant_id, key):
        return self.files[(tenant_id, key)]

    def delete(self, tenant_id, key):
        self.files.pop((tenant_id, key), None)


class MemoryIndex:
    def __init__(self):
        self.passages: dict[tuple[str, str], tuple[int, list]] = {}

    def replace(self, tenant_id, document_id, version, passages):
        self.passages[(tenant_id, document_id)] = (version, list(passages))

    def remove(self, tenant_id, document_id):
        self.passages.pop((tenant_id, document_id), None)

    def count(self, tenant_id, document_id):
        return len(self.passages.get((tenant_id, document_id), (0, []))[1])

    def texts(self, tenant_id):
        return [p.text for (t, _), (_, ps) in self.passages.items() if t == tenant_id for p in ps]


class MemoryCatalog:
    def __init__(self):
        self.docs: dict[str, DocumentRecord] = {}
        self.vers: dict[tuple[str, int], VersionRecord] = {}

    def add(self, document, version):
        self.docs[document.document_id] = document
        self.vers[(version.document_id, version.version)] = version

    def save_document(self, document):
        self.docs[document.document_id] = document

    def save_version(self, version):
        self.vers[(version.document_id, version.version)] = version

    def document(self, tenant_id, document_id):
        document = self.docs.get(document_id)
        return document if document and document.tenant_id == tenant_id else None

    def documents(self, tenant_id):
        found = [d for d in self.docs.values() if d.tenant_id == tenant_id and not d.deleted]
        return sorted(found, key=lambda d: d.created_at, reverse=True)

    def by_filename(self, tenant_id, filename):
        return next((d for d in self.documents(tenant_id) if d.filename.lower() == filename.lower()), None)

    def by_content(self, tenant_id, sha256):
        return next((v for v in self.vers.values()
                     if v.job.tenant_id == tenant_id and v.sha256 == sha256 and v.job.state in ACTIVE
                     and not self.docs[v.document_id].deleted), None)

    def version(self, tenant_id, document_id, version):
        found = self.vers.get((document_id, version))
        return found if found and found.job.tenant_id == tenant_id else None

    def versions(self, tenant_id, document_id):
        return sorted((v for v in self.vers.values()
                       if v.document_id == document_id and v.job.tenant_id == tenant_id),
                      key=lambda v: v.version)

    def job(self, job_id):
        return next((v for v in self.vers.values() if v.job.job_id == job_id), None)

    def unfinished_jobs(self):
        return [v for v in self.vers.values() if v.job.state in UNFINISHED]
