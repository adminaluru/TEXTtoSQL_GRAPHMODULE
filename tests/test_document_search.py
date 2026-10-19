"""Step 9: passage search on SQLite, end to end with ingestion, and the search route."""
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from hybrid_contracts import AccessContext, RetrievalRequest, Route

from adapters.document_files import LocalFolderFileStore
from adapters.document_search import SqliteDocumentDirectory, SqlitePassageSearch
from adapters.document_sqlite import SqliteDatabase, SqliteDocumentCatalog, SqlitePassageIndex
from api.application import create_app
from document_ingestion import DocumentIngestionService, Location, Passage
from document_retrieval import NO_MATCH, NO_TERMS, DocumentRetriever
from tests.document_test_files import make_docx, make_pdf, make_xlsx
from tests.test_api_identity import SETTINGS, headers, token
from trusted_identity import IdentityVerifier

ACME = AccessContext(subject_id="alice", tenant_id="tenant-acme", policy_version="test")
NOVA = AccessContext(subject_id="bob", tenant_id="tenant-nova", policy_version="test")


class Docs:
    """Real ingestion and retrieval on a temporary SQLite file and folder."""

    def __init__(self, tmp_path):
        self.db = SqliteDatabase(tmp_path / "catalog.sqlite3")
        self.queue = []
        self.service = DocumentIngestionService(
            SqliteDocumentCatalog(self.db), LocalFolderFileStore(tmp_path / "files"),
            SqlitePassageIndex(self.db), enqueue=self.queue.append)
        self.retriever = DocumentRetriever(SqlitePassageSearch(self.db), SqliteDocumentDirectory(self.db))

    def add(self, access, filename, data):
        document_id = self.service.upload(access, filename, data).document.document_id
        while self.queue:
            self.service.process(self.queue.pop(0))
        return document_id

    def ask(self, access, question, limit=5):
        return self.retriever.retrieve(RetrievalRequest(
            request_id="r1", question=question, access=access, route=Route.DOCUMENT,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10), max_evidence_items=limit))


@pytest.fixture
def docs(tmp_path):
    d = Docs(tmp_path)
    yield d
    d.db.close()


def texts(result):
    return [(e.sources[0].location, e.content) for e in result.evidence]


# ==========================================================
# Search quality and citations
# ==========================================================

def test_word_endings_match_and_citations_name_file_and_place(docs):
    docs.add(ACME, "terms.docx", make_docx(lambda d: (
        d.add_heading("Payment terms", level=1),
        d.add_paragraph("Late payments incur a fee of 2% per month."),
        d.add_heading("Delivery", level=1),
        d.add_paragraph("Goods ship within 5 days."))))
    result = docs.ask(ACME, "What fee applies to a late payment?")
    assert result.status == "complete"
    assert texts(result)[0] == ("terms.docx · section “Payment terms”, paragraphs 1–2",
                                "Payment terms\nLate payments incur a fee of 2% per month.")


def test_better_matches_rank_first(docs):
    docs.add(ACME, "a.pdf", make_pdf(["The invoice is attached.", "Invoice disputes must name the claim amount."]))
    result = docs.ask(ACME, "invoice dispute claim amount")
    assert texts(result)[0] == ("a.pdf · page 2", "Invoice disputes must name the claim amount.")


def test_excel_rows_are_found_with_their_sheet_and_rows(docs):
    def build(workbook):
        workbook.active.title = "Claims"
        workbook.active.append(["Claim", "Customer", "Amount"])
        workbook.active.append(["CL-77", "Blue Prism", 500])
    docs.add(ACME, "claims.xlsx", make_xlsx(build))
    [(citation, text)] = texts(docs.ask(ACME, "CL-77 amount"))
    assert citation == "claims.xlsx · sheet “Claims”, rows 1–2"
    assert "Claim: CL-77; Customer: Blue Prism; Amount: 500" in text


def test_no_match_and_no_words_are_explicit(docs):
    docs.add(ACME, "a.pdf", make_pdf(["Payment terms"]))
    assert (docs.ask(ACME, "warranty").status, docs.ask(ACME, "warranty").warnings) == ("empty", (NO_MATCH,))
    assert (docs.ask(ACME, "what is the").status, docs.ask(ACME, "what is the").warnings) == ("clarification", (NO_TERMS,))


def test_search_syntax_typed_by_a_user_is_just_words(docs):
    docs.add(ACME, "a.pdf", make_pdf(["Payment terms"]))
    for question in ['payment" OR tenant_id:*', "payment NEAR(terms)", "payment' --", "*", "^payment"]:
        result = docs.ask(ACME, question)
        assert result.status in ("complete", "empty", "clarification")


# ==========================================================
# Workspace isolation, replacement and deletion
# ==========================================================

def test_search_never_returns_another_workspaces_passages(docs):
    docs.add(ACME, "acme.pdf", make_pdf(["Acme rebate is 3 percent."]))
    docs.add(NOVA, "nova.pdf", make_pdf(["Nova rebate is 9 percent."]))
    assert [c for c, _ in texts(docs.ask(ACME, "rebate percent"))] == ["acme.pdf · page 1"]
    assert [c for c, _ in texts(docs.ask(NOVA, "rebate percent"))] == ["nova.pdf · page 1"]


def test_replaced_and_deleted_documents_stop_appearing(docs):
    document_id = docs.add(ACME, "terms.pdf", make_pdf(["Rebate is 3 percent."]))
    docs.add(ACME, "terms.pdf", make_pdf(["Rebate is 4 percent."]))
    [(citation, text)] = texts(docs.ask(ACME, "rebate"))
    assert text == "Rebate is 4 percent."
    assert docs.ask(ACME, "rebate").evidence[0].sources[0].version == "2"

    docs.service.delete(ACME, document_id)
    assert docs.ask(ACME, "rebate").status == "empty"


def test_stale_index_rows_are_dropped_by_the_directory_check(docs):
    document_id = docs.add(ACME, "terms.pdf", make_pdf(["Rebate is 3 percent."]))
    # Simulate an index row left behind for an old version.
    SqlitePassageIndex(docs.db).replace("tenant-acme", document_id, 9, [Passage(1, "Rebate stale", Location("page", 1, 1))])
    assert docs.ask(ACME, "rebate").status == "empty"


def test_a_broken_index_reports_unavailable(docs, monkeypatch):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(docs.db, "query", broken)
    result = docs.ask(ACME, "rebate")
    assert result.status == "unavailable"


# ==========================================================
# Upgrade of an index created before word-ending matching
# ==========================================================

def test_existing_passages_are_kept_when_the_index_is_upgraded(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    old = sqlite3.connect(path)
    old.execute("CREATE VIRTUAL TABLE passages USING fts5 (text, tenant_id UNINDEXED, document_id UNINDEXED, "
                "version UNINDEXED, ordinal UNINDEXED, location_kind UNINDEXED, location_name UNINDEXED, "
                "location_start UNINDEXED, location_end UNINDEXED)")
    old.execute("INSERT INTO passages VALUES ('Late payments cost 2%', 'tenant-acme', 'doc_1', 1, 1, 'page', NULL, 1, 1)")
    old.commit()
    old.close()

    db = SqliteDatabase(path)
    try:
        assert "porter" in db.query("SELECT sql FROM sqlite_master WHERE name='passages'")[0][0]
        [hit] = SqlitePassageSearch(db).search("tenant-acme", ["payment"], 5)
        assert hit.text == "Late payments cost 2%" and hit.location == "page 1"
        assert not db.query("SELECT name FROM sqlite_master WHERE name LIKE 'passages_before%'")
    finally:
        db.close()


# ==========================================================
# HTTP route
# ==========================================================

@pytest.fixture
def client(docs):
    app = create_app(Mock(), identity=IdentityVerifier(SETTINGS), agent_for=lambda a: Mock(),
                     rate_limit_requests_per_minute=1000, documents=docs.service, document_search=docs.retriever)
    return TestClient(app)


AUTH_ACME = headers(token(("tenant-acme",)))
AUTH_NOVA = headers(token(("tenant-nova",), subject="bob"))


def test_search_route_returns_quoted_passages_with_citations(docs, client):
    docs.add(ACME, "terms.pdf", make_pdf(["Late payment fee is 2% per month."]))
    response = client.post("/documents/search", headers=AUTH_ACME, json={"query": "late payment fee"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete" and body["terms"] == ["late", "payment", "fee"]
    assert body["results"][0]["citation"] == "terms.pdf · page 1"
    assert body["results"][0]["text"] == "Late payment fee is 2% per month."
    assert body["results"][0]["version"] == 1


def test_search_route_is_scoped_to_the_callers_workspace(docs, client):
    docs.add(ACME, "acme.pdf", make_pdf(["Acme rebate 3 percent"]))
    body = client.post("/documents/search", headers=AUTH_NOVA, json={"query": "rebate"}).json()
    assert body["status"] == "empty" and body["results"] == [] and body["message"] == NO_MATCH


def test_search_route_needs_a_token_and_a_sensible_request(client):
    assert client.post("/documents/search", json={"query": "fee"}).status_code == 401
    assert client.post("/documents/search", headers=AUTH_ACME, json={"query": ""}).status_code == 422
    assert client.post("/documents/search", headers=AUTH_ACME, json={"query": "fee", "limit": 99}).status_code == 422
    assert client.post("/documents/search", headers=AUTH_ACME,
                       json={"query": "fee", "tenant": "tenant-nova"}).status_code == 422


def test_search_route_explains_unhelpful_questions(client):
    body = client.post("/documents/search", headers=AUTH_ACME, json={"query": "what is the"}).json()
    assert body["status"] == "clarification" and body["message"] == NO_TERMS


def test_search_route_is_absent_without_a_retriever(docs):
    app = create_app(Mock(), identity=IdentityVerifier(SETTINGS), agent_for=lambda a: Mock(), documents=docs.service)
    response = TestClient(app).post("/documents/search", headers=AUTH_ACME, json={"query": "fee"})
    assert response.status_code in (404, 405)
