"""Passage search and document directory on the local SQLite index (Step 9).

Implements the two ports of the document_retrieval package:
    SqlitePassageSearch     FTS5 full-text search, best matches first (BM25)
    SqliteDocumentDirectory which documents may be shown, and which version

Every query filters on the tenant. Search words reach SQLite as a bound
parameter, each one quoted, so they can never change the query.
"""
import sqlite3
from typing import Sequence

from adapters.document_sqlite import SqliteDatabase
from document_ingestion import Location
from document_retrieval import ActiveDocument, PassageHit


class SqlitePassageSearch:
    def __init__(self, database: SqliteDatabase):
        self._db = database

    def search(self, tenant_id: str, terms: Sequence[str], limit: int) -> list[PassageHit]:
        if not terms:
            return []
        # Any word may match; passages matching more and rarer words rank first.
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        try:
            rows = self._db.query(
                "SELECT tenant_id, document_id, version, ordinal, text, location_kind, location_name, "
                "location_start, location_end, bm25(passages) AS score "
                "FROM passages WHERE passages MATCH ? AND tenant_id = ? "
                "ORDER BY score, document_id, ordinal LIMIT ?",
                (match, tenant_id, limit))
        except sqlite3.Error as exc:
            raise ConnectionError("document index unavailable") from exc
        return [
            PassageHit(
                tenant_id=row["tenant_id"], document_id=row["document_id"], version=int(row["version"]),
                ordinal=int(row["ordinal"]), text=row["text"],
                location=Location(row["location_kind"], int(row["location_start"]), int(row["location_end"]),
                                  row["location_name"]).label,
                score=float(row["score"]),
            )
            for row in rows
        ]


class SqliteDocumentDirectory:
    def __init__(self, database: SqliteDatabase):
        self._db = database

    def has_searchable(self, tenant_id: str) -> bool:
        """At least one document of this tenant has a ready version (cheap check before routing)."""
        try:
            rows = self._db.query(
                "SELECT 1 FROM documents WHERE tenant_id = ? AND deleted = 0 AND active_version IS NOT NULL LIMIT 1",
                (tenant_id,))
        except sqlite3.Error as exc:
            raise ConnectionError("document catalog unavailable") from exc
        return bool(rows)

    def active_documents(self, tenant_id: str, document_ids: Sequence[str]) -> dict[str, ActiveDocument]:
        if not document_ids:
            return {}
        marks = ",".join("?" * len(document_ids))
        try:
            rows = self._db.query(
                "SELECT document_id, filename, active_version FROM documents "
                f"WHERE tenant_id = ? AND deleted = 0 AND active_version IS NOT NULL AND document_id IN ({marks})",
                (tenant_id, *document_ids))
        except sqlite3.Error as exc:
            raise ConnectionError("document catalog unavailable") from exc
        return {r["document_id"]: ActiveDocument(r["document_id"], r["filename"], int(r["active_version"]))
                for r in rows}
