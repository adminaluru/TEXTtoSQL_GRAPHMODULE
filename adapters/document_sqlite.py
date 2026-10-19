"""Document catalog and passage index in one local SQLite file.

SQLite ships with Python, and its FTS5 full-text search is enough for the
POC (Step 9 decides the search backend). Every query filters on the tenant.

Two classes, one per port, sharing one database connection:
    SqliteDocumentCatalog  documents, versions and their processing jobs
    SqlitePassageIndex     searchable passages (FTS5)
"""
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Sequence

from document_ingestion import DocumentRecord, Passage, VersionRecord
from hybrid_contracts import DocumentJob, DocumentState

_ACTIVE = (DocumentState.QUEUED.value, DocumentState.PROCESSING.value, DocumentState.READY.value)
_UNFINISHED = (DocumentState.QUEUED.value, DocumentState.PROCESSING.value, DocumentState.DELETING.value)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id    TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    filename       TEXT NOT NULL,
    filename_key   TEXT NOT NULL,
    uploaded_by    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    latest_version INTEGER NOT NULL,
    active_version INTEGER,
    deleted        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS documents_by_tenant ON documents (tenant_id, deleted, filename_key);

CREATE TABLE IF NOT EXISTS versions (
    document_id   TEXT NOT NULL REFERENCES documents (document_id),
    version       INTEGER NOT NULL,
    tenant_id     TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    file_type     TEXT NOT NULL,
    file_key      TEXT NOT NULL,
    uploaded_by   TEXT NOT NULL,
    job_id        TEXT NOT NULL UNIQUE,
    state         TEXT NOT NULL,
    job           TEXT NOT NULL,
    notes         TEXT NOT NULL,
    passage_count INTEGER NOT NULL,
    failure_code  TEXT,
    PRIMARY KEY (document_id, version)
);
CREATE INDEX IF NOT EXISTS versions_by_content ON versions (tenant_id, sha256);
CREATE INDEX IF NOT EXISTS versions_by_state ON versions (state);

CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5 (
    text,
    tenant_id UNINDEXED,
    document_id UNINDEXED,
    version UNINDEXED,
    ordinal UNINDEXED,
    location_kind UNINDEXED,
    location_name UNINDEXED,
    location_start UNINDEXED,
    location_end UNINDEXED,
    -- "porter": payment / payments / paid-style word endings match each other.
    tokenize = 'porter unicode61 remove_diacritics 2'
);
"""

_OLD_PASSAGES = "passages_before_porter"


class SqliteDatabase:
    """One connection, used by one thread at a time."""

    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            # One-time upgrade: passages indexed before word-ending matching
            # existed are copied into the new index.
            existing = self.connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'passages'").fetchone()
            upgrade = existing is not None and "porter" not in existing[0]
            if upgrade:
                self.connection.execute(f"ALTER TABLE passages RENAME TO {_OLD_PASSAGES}")
            self.connection.executescript(_SCHEMA)
            if upgrade:
                self.connection.executescript(
                    f"BEGIN; INSERT INTO passages SELECT * FROM {_OLD_PASSAGES}; "
                    f"DROP TABLE {_OLD_PASSAGES}; COMMIT;")

    def transaction(self):
        return _Transaction(self)

    def query(self, sql: str, params: Sequence = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.connection.execute(sql, params).fetchall()

    def close(self) -> None:
        with self.lock:
            self.connection.close()


class _Transaction:
    def __init__(self, database: SqliteDatabase):
        self._database = database

    def __enter__(self) -> sqlite3.Connection:
        self._database.lock.acquire()
        self._database.connection.execute("BEGIN IMMEDIATE")
        return self._database.connection

    def __exit__(self, kind, value, traceback):
        try:
            self._database.connection.execute("COMMIT" if kind is None else "ROLLBACK")
        finally:
            self._database.lock.release()


# ==========================================================
# Catalog
# ==========================================================

class SqliteDocumentCatalog:
    def __init__(self, database: SqliteDatabase):
        self._db = database

    def add(self, document: DocumentRecord, version: VersionRecord) -> None:
        with self._db.transaction() as sql:
            _upsert_document(sql, document)
            sql.execute(
                "INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                _version_row(version))

    def save_document(self, document: DocumentRecord) -> None:
        with self._db.transaction() as sql:
            _upsert_document(sql, document)

    def save_version(self, version: VersionRecord) -> None:
        row = _version_row(version)
        with self._db.transaction() as sql:
            sql.execute(
                "UPDATE versions SET state=?, job=?, notes=?, passage_count=?, failure_code=? "
                "WHERE document_id=? AND version=? AND tenant_id=?",
                (row[9], row[10], row[11], row[12], row[13], row[0], row[1], row[2]))

    def document(self, tenant_id: str, document_id: str) -> DocumentRecord | None:
        rows = self._db.query("SELECT * FROM documents WHERE tenant_id=? AND document_id=?",
                              (tenant_id, document_id))
        return _document(rows[0]) if rows else None

    def documents(self, tenant_id: str) -> list[DocumentRecord]:
        rows = self._db.query(
            "SELECT * FROM documents WHERE tenant_id=? AND deleted=0 ORDER BY created_at DESC, document_id",
            (tenant_id,))
        return [_document(r) for r in rows]

    def by_filename(self, tenant_id: str, filename: str) -> DocumentRecord | None:
        rows = self._db.query(
            "SELECT * FROM documents WHERE tenant_id=? AND deleted=0 AND filename_key=?",
            (tenant_id, filename.casefold()))
        return _document(rows[0]) if rows else None

    def by_content(self, tenant_id: str, sha256: str) -> VersionRecord | None:
        rows = self._db.query(
            "SELECT v.* FROM versions v JOIN documents d ON d.document_id = v.document_id "
            "WHERE v.tenant_id=? AND d.tenant_id=? AND v.sha256=? AND d.deleted=0 "
            f"AND v.state IN ({','.join('?' * len(_ACTIVE))}) ORDER BY v.version DESC LIMIT 1",
            (tenant_id, tenant_id, sha256, *_ACTIVE))
        return _version(rows[0]) if rows else None

    def version(self, tenant_id: str, document_id: str, version: int) -> VersionRecord | None:
        rows = self._db.query(
            "SELECT * FROM versions WHERE tenant_id=? AND document_id=? AND version=?",
            (tenant_id, document_id, version))
        return _version(rows[0]) if rows else None

    def versions(self, tenant_id: str, document_id: str) -> list[VersionRecord]:
        rows = self._db.query(
            "SELECT * FROM versions WHERE tenant_id=? AND document_id=? ORDER BY version",
            (tenant_id, document_id))
        return [_version(r) for r in rows]

    def job(self, job_id: str) -> VersionRecord | None:
        rows = self._db.query("SELECT * FROM versions WHERE job_id=?", (job_id,))
        return _version(rows[0]) if rows else None

    def unfinished_jobs(self) -> list[VersionRecord]:
        rows = self._db.query(
            f"SELECT * FROM versions WHERE state IN ({','.join('?' * len(_UNFINISHED))}) "
            "ORDER BY document_id, version", _UNFINISHED)
        return [_version(r) for r in rows]


def _upsert_document(sql: sqlite3.Connection, d: DocumentRecord) -> None:
    sql.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (document_id) DO UPDATE SET latest_version=excluded.latest_version, "
        "active_version=excluded.active_version, deleted=excluded.deleted "
        "WHERE documents.tenant_id = excluded.tenant_id",
        (d.document_id, d.tenant_id, d.filename, d.filename.casefold(), d.uploaded_by,
         d.created_at.isoformat(), d.latest_version, d.active_version, int(d.deleted)))


def _document(row: sqlite3.Row) -> DocumentRecord:
    return DocumentRecord(
        document_id=row["document_id"], tenant_id=row["tenant_id"], filename=row["filename"],
        uploaded_by=row["uploaded_by"], created_at=datetime.fromisoformat(row["created_at"]),
        latest_version=row["latest_version"], active_version=row["active_version"],
        deleted=bool(row["deleted"]))


def _version_row(v: VersionRecord) -> tuple:
    return (v.document_id, v.version, v.job.tenant_id, v.sha256, v.size_bytes, v.file_type,
            v.file_key, v.uploaded_by, v.job.job_id, v.job.state.value, v.job.model_dump_json(),
            json.dumps(list(v.notes)), v.passage_count, v.failure_code)


def _version(row: sqlite3.Row) -> VersionRecord:
    return VersionRecord(
        document_id=row["document_id"], version=row["version"], sha256=row["sha256"],
        size_bytes=row["size_bytes"], file_type=row["file_type"], file_key=row["file_key"],
        uploaded_by=row["uploaded_by"], job=DocumentJob.model_validate_json(row["job"]),
        notes=tuple(json.loads(row["notes"])), passage_count=row["passage_count"],
        failure_code=row["failure_code"])


# ==========================================================
# Passage index
# ==========================================================

class SqlitePassageIndex:
    def __init__(self, database: SqliteDatabase):
        self._db = database

    def replace(self, tenant_id: str, document_id: str, version: int,
                passages: Sequence[Passage]) -> None:
        # One transaction: searches see either the old passages or the new ones.
        with self._db.transaction() as sql:
            sql.execute("DELETE FROM passages WHERE tenant_id=? AND document_id=?",
                        (tenant_id, document_id))
            sql.executemany(
                "INSERT INTO passages VALUES (?,?,?,?,?,?,?,?,?)",
                [(p.text, tenant_id, document_id, version, p.ordinal, p.location.kind,
                  p.location.name, p.location.start, p.location.end) for p in passages])

    def remove(self, tenant_id: str, document_id: str) -> None:
        with self._db.transaction() as sql:
            sql.execute("DELETE FROM passages WHERE tenant_id=? AND document_id=?",
                        (tenant_id, document_id))

    def count(self, tenant_id: str, document_id: str) -> int:
        rows = self._db.query("SELECT COUNT(*) FROM passages WHERE tenant_id=? AND document_id=?",
                              (tenant_id, document_id))
        return rows[0][0]
