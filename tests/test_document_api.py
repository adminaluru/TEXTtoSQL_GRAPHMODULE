"""A8.4: /documents routes (jwt mode): workspace isolation, limits, lifecycle."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from adapters.document_files import LocalFolderFileStore
from adapters.document_sqlite import SqliteDatabase, SqliteDocumentCatalog, SqlitePassageIndex
from adapters.document_worker import DocumentWorker
from api.application import create_app
from document_ingestion import DocumentIngestionService, IngestionLimits
from tests.document_test_files import make_docx, make_pdf
from tests.test_api_identity import SETTINGS, headers, token
from trusted_identity import IdentityVerifier

LIMIT = 200_000
ACME = headers(token(("tenant-acme",)))
NOVA = headers(token(("tenant-nova",), subject="bob"))
BOTH = token(("tenant-acme", "tenant-nova"), subject="carol")


class Setup:
    def __init__(self, tmp_path, service=None):
        self.database = SqliteDatabase(tmp_path / "catalog.sqlite3")
        self.index = SqlitePassageIndex(self.database)
        self.worker = DocumentWorker()
        self.service = service or DocumentIngestionService(
            SqliteDocumentCatalog(self.database), LocalFolderFileStore(tmp_path / "files"), self.index,
            enqueue=self.worker.enqueue, limits=IngestionLimits(max_file_bytes=LIMIT))
        self.worker.start(self.service.process)
        app = create_app(Mock(), identity=IdentityVerifier(SETTINGS), agent_for=lambda access: Mock(),
                         rate_limit_requests_per_minute=1000, documents=self.service,
                         document_max_file_bytes=LIMIT)
        self.client = TestClient(app)

    def upload(self, data, name="terms.pdf", auth=ACME):
        return self.client.post("/documents", headers=auth, files={"file": (name, data, "application/octet-stream")})

    def settle(self):
        self.worker.wait_until_idle()

    def close(self):
        self.worker.stop()
        self.database.close()


@pytest.fixture
def api(tmp_path):
    setup = Setup(tmp_path)
    yield setup
    setup.close()


def test_upload_is_accepted_then_becomes_searchable(api):
    response = api.upload(make_pdf(["Payment due in 30 days", "Late fee 2%"]))
    assert response.status_code == 202
    body = response.json()
    assert body["filename"] == "terms.pdf" and body["duplicate"] is False
    assert body["latest"]["status"] in ("queued", "processing", "ready")

    api.settle()
    listing = api.client.get("/documents", headers=ACME).json()["documents"]
    assert len(listing) == 1
    assert listing[0]["searchable"] is True
    assert listing[0]["latest"]["status"] == "ready"
    assert listing[0]["latest"]["passage_count"] == 2
    assert listing[0]["latest"]["file_type"] == "pdf"

    detail = api.client.get(f"/documents/{body['document_id']}", headers=ACME).json()
    assert [v["version"] for v in detail["versions"]] == [1]


def test_every_route_requires_a_valid_token(api):
    document_id = api.upload(make_pdf(["x"])).json()["document_id"]
    calls = [
        lambda h: api.client.post("/documents", headers=h, files={"file": ("a.pdf", make_pdf(["y"]))}),
        lambda h: api.client.get("/documents", headers=h),
        lambda h: api.client.get(f"/documents/{document_id}", headers=h),
        lambda h: api.client.post(f"/documents/{document_id}/retry", headers=h),
        lambda h: api.client.delete(f"/documents/{document_id}", headers=h),
    ]
    for call in calls:
        assert call({}).status_code == 401
        assert call(headers("not-a-token")).status_code == 401


def test_other_workspaces_cannot_see_or_touch_a_document(api):
    document_id = api.upload(make_pdf(["Acme only"])).json()["document_id"]
    api.settle()
    assert api.client.get("/documents", headers=NOVA).json() == {"documents": []}
    for response in (api.client.get(f"/documents/{document_id}", headers=NOVA),
                     api.client.delete(f"/documents/{document_id}", headers=NOVA),
                     api.client.post(f"/documents/{document_id}/retry", headers=NOVA)):
        assert response.status_code == 404
        assert response.json()["detail"] == "Document not found."
    assert api.client.get(f"/documents/{document_id}", headers=ACME).status_code == 200


def test_workspace_is_chosen_from_the_token_memberships_only(api):
    in_nova = api.upload(make_pdf(["for nova"]), auth=headers(BOTH, tenant="tenant-nova")).json()
    api.settle()
    assert api.client.get("/documents", headers=NOVA).json()["documents"][0]["document_id"] == in_nova["document_id"]
    assert api.client.get("/documents", headers=ACME).json() == {"documents": []}
    # A tenant the token does not include is refused before anything is stored.
    response = api.upload(make_pdf(["sneaky"]), auth=headers(token(("tenant-acme",)), tenant="tenant-vertex"))
    assert response.status_code == 403


def test_oversized_uploads_are_refused(api):
    response = api.upload(b"%PDF-" + b"0" * (LIMIT + 100_000))
    assert response.status_code == 413
    assert response.json()["detail"] == "The file is larger than 0 MB."  # LIMIT is below 1 MB in this test
    # Just over the limit but within the form overhead: refused after reading.
    response = api.upload(b"%PDF-" + b"0" * LIMIT)
    assert response.status_code == 413


def test_upload_without_declared_size_is_refused(api):
    def chunks():
        yield b"--x\r\n"
    response = api.client.post("/documents", headers={**ACME, "Content-Type": "multipart/form-data; boundary=x"},
                               content=chunks())
    assert response.status_code == 411


def test_upload_needs_a_file_field(api):
    response = api.client.post("/documents", headers=ACME, data={"note": "no file"})
    assert response.status_code == 400
    assert "named \"file\"" in response.json()["detail"]


@pytest.mark.parametrize("name,data,status,text", [
    ("terms.docx", make_pdf(["x"]), 400, "not a .docx file"),
    ("terms.exe", make_pdf(["x"]), 400, "Only Word"),
    ("macro.xlsm", make_docx(lambda d: d.add_paragraph("x")), 400, "macros"),
], ids=["renamed", "exe", "macro"])
def test_wrong_files_are_refused_with_a_safe_message(api, name, data, status, text):
    response = api.upload(data, name=name)
    assert response.status_code == status
    assert text in response.json()["detail"] and response.json()["request_id"]
    assert api.client.get("/documents", headers=ACME).json() == {"documents": []}


def test_processing_failure_is_visible_and_only_technical_failures_retry(api):
    document_id = api.upload(make_pdf([None]), name="scan.pdf").json()["document_id"]
    api.settle()
    latest = api.client.get(f"/documents/{document_id}", headers=ACME).json()["latest"]
    assert latest["status"] == "failed" and "scanned" in latest["message"]
    assert latest["can_retry"] is False
    response = api.client.post(f"/documents/{document_id}/retry", headers=ACME)
    assert response.status_code == 409


def test_duplicate_upload_is_reported(api):
    first = api.upload(make_pdf(["same"])).json()
    again = api.upload(make_pdf(["same"]), name="copy.pdf").json()
    assert again["duplicate"] is True and again["document_id"] == first["document_id"]


def test_delete_removes_the_document_and_its_passages(api):
    document_id = api.upload(make_pdf(["remove me"])).json()["document_id"]
    api.settle()
    assert api.index.count("tenant-acme", document_id) == 1
    response = api.client.delete(f"/documents/{document_id}", headers=ACME)
    assert response.status_code == 204 and response.content == b""
    assert api.index.count("tenant-acme", document_id) == 0
    assert api.client.get(f"/documents/{document_id}", headers=ACME).status_code == 404
    assert api.client.get("/documents", headers=ACME).json() == {"documents": []}


def test_unexpected_errors_are_hidden(tmp_path):
    service = Mock()
    service.documents.side_effect = RuntimeError("database file C:\\secret\\catalog is locked")
    setup = Setup(tmp_path, service=service)
    try:
        response = setup.client.get("/documents", headers=ACME)
        assert response.status_code == 500
        assert response.json()["detail"] == "The document request could not be completed."
        assert "secret" not in response.text
    finally:
        setup.close()


def test_documents_need_jwt_mode_and_are_absent_otherwise(tmp_path):
    service = Mock()
    with pytest.raises(ValueError, match="AUTH_MODE=jwt"):
        create_app(Mock(), api_key="k", documents=service)
    client = TestClient(create_app(Mock(), identity=IdentityVerifier(SETTINGS), agent_for=lambda a: Mock()))
    assert client.get("/documents", headers=ACME).status_code == 404


def test_cors_allows_delete_for_listed_origins_only(tmp_path):
    app = create_app(Mock(), identity=IdentityVerifier(SETTINGS), agent_for=lambda a: Mock(),
                     cors_allowed_origins=["https://ui.example.com"])
    client = TestClient(app)
    preflight = {"Origin": "https://ui.example.com", "Access-Control-Request-Method": "DELETE"}
    assert client.options("/documents/x", headers=preflight).status_code == 200
    preflight["Origin"] = "https://evil.example.com"
    assert client.options("/documents/x", headers=preflight).status_code == 400
