"""What the retriever needs from storage, as interfaces ("ports").

Both calls take the tenant: an adapter is never asked for, and never
returns, another workspace's passages.
"""
from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class PassageHit:
    tenant_id: str
    document_id: str
    version: int
    ordinal: int
    text: str
    # Human-readable place in the file, e.g. "page 3" or "sheet “Q1”, rows 2–15".
    location: str
    # Lower is a better match (BM25 as SQLite reports it).
    score: float


@dataclass(frozen=True)
class ActiveDocument:
    document_id: str
    filename: str
    # The version whose passages may be shown; anything else is stale.
    active_version: int


class PassageSearch(Protocol):
    def search(self, tenant_id: str, terms: Sequence[str], limit: int) -> list[PassageHit]:
        """Best matches first, within this tenant only."""


class DocumentDirectory(Protocol):
    def active_documents(self, tenant_id: str, document_ids: Sequence[str]) -> dict[str, ActiveDocument]:
        """Documents of this tenant that exist, are not deleted and have a ready version."""
