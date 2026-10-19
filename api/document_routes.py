"""HTTP routes for documents (Step 8). jwt mode only.

    POST   /documents                 upload one file (multipart field "file") -> 202
    GET    /documents                 this workspace's documents and their status
    GET    /documents/{id}            one document with all its versions
    POST   /documents/{id}/retry      retry after a technical failure -> 202
    DELETE /documents/{id}            delete; its passages stop being searchable -> 204
    POST   /documents/search          quoted passages with citations (Step 9)

The workspace is the verified caller's tenant. Another workspace's
document answers 404, exactly like a missing one.

Uploads: the caller is authenticated and the declared size checked
*before* the body is read, so an anonymous or oversized upload costs
nothing. Processing happens in the background; poll GET /documents.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from api.identity import authenticate_request
from api.models import (APIErrorResponse, DocumentDetail, DocumentInfo, DocumentList, DocumentPassage,
                        DocumentSearchRequest, DocumentSearchResponse, DocumentVersionInfo)
from document_ingestion import DocumentIngestionService, DocumentNotFound, DocumentRejected
from document_retrieval import DocumentRetriever, search_terms
from hybrid_contracts import RetrievalRequest, Route
from observability.perf import new_request_id
from trusted_identity import IdentityVerifier

logger = logging.getLogger("uvicorn.error")

# Room for the multipart envelope around the file itself.
_FORM_OVERHEAD_BYTES = 64 * 1024

_STATUS_FOR_CODE = {"too_large": 413, "not_retryable": 409}


# Search outcomes that are not answers become errors with the retriever's safe message.
_SEARCH_ERROR_STATUS = {"denied": 403, "unavailable": 503, "failed": 500, "unsupported": 400, "disabled": 404}


def add_document_routes(app: FastAPI, identity: IdentityVerifier,
                        documents: DocumentIngestionService, max_file_bytes: int,
                        retriever: DocumentRetriever | None = None,
                        search_timeout_seconds: float = 10.0) -> None:
    router = APIRouter(prefix="/documents", tags=["documents"])
    errors = {status: {"model": APIErrorResponse} for status in (400, 401, 403, 404, 409, 411, 413, 500)}
    too_large = f"The file is larger than {max_file_bytes // (1024 * 1024)} MB."

    def describe(document, version, duplicate=False, versions=None):
        info = dict(
            document_id=document.document_id,
            filename=document.filename,
            searchable=document.active_version is not None,
            active_version=document.active_version,
            latest=_version_info(documents, version),
            duplicate=duplicate,
        )
        if versions is None:
            return DocumentInfo(**info)
        return DocumentDetail(**info, versions=[_version_info(documents, v) for v in versions])

    @router.post("", status_code=202, response_model=DocumentInfo, responses=errors)
    async def upload(request: Request):
        request_id = new_request_id()
        access = authenticate_request(identity, request)

        declared = request.headers.get("content-length", "")
        if not declared.isdigit():
            return _error(411, "The upload must declare its size (Content-Length).", request_id)
        if int(declared) > max_file_bytes + _FORM_OVERHEAD_BYTES:
            return _error(413, too_large, request_id)

        form = await request.form(max_files=1, max_fields=1)
        try:
            upload_file = form.get("file")
            if not isinstance(upload_file, UploadFile):
                return _error(400, "Send the file in a form field named \"file\".", request_id)
            data = await upload_file.read(max_file_bytes + 1)
            if len(data) > max_file_bytes:
                return _error(413, too_large, request_id)
            filename = upload_file.filename or ""
        finally:
            await form.close()

        return await run_in_threadpool(
            _safely, request_id,
            lambda: describe(*_uploaded(documents.upload(access, filename, data))))

    if retriever is not None:

        @router.post("/search", response_model=DocumentSearchResponse, responses=errors)
        def search(body: DocumentSearchRequest, request: Request):
            request_id = new_request_id()
            access = authenticate_request(identity, request)

            def run():
                result = retriever.retrieve(RetrievalRequest(
                    request_id=request_id,
                    question=body.query,
                    access=access,
                    route=Route.DOCUMENT,
                    deadline_at=datetime.now(timezone.utc) + timedelta(seconds=search_timeout_seconds),
                    max_evidence_items=body.limit,
                ))
                if result.status in _SEARCH_ERROR_STATUS:
                    return _error(_SEARCH_ERROR_STATUS[result.status], result.warnings[0], request_id)
                return DocumentSearchResponse(
                    status=result.status,
                    message=result.warnings[0] if result.warnings else None,
                    terms=search_terms(body.query),
                    results=[
                        DocumentPassage(document_id=e.sources[0].source_id, version=int(e.sources[0].version),
                                        citation=e.sources[0].location, text=e.content)
                        for e in result.evidence
                    ],
                )
            return _safely(request_id, run)

    @router.get("", response_model=DocumentList, responses=errors)
    def list_documents(request: Request):
        access = authenticate_request(identity, request)
        return _safely(new_request_id(), lambda: DocumentList(
            documents=[describe(d, v) for d, v in documents.documents(access)]))

    @router.get("/{document_id}", response_model=DocumentDetail, responses=errors)
    def get_document(document_id: str, request: Request):
        access = authenticate_request(identity, request)

        def detail():
            document, versions = documents.document(access, document_id)
            return describe(document, versions[-1], versions=versions)
        return _safely(new_request_id(), detail)

    @router.post("/{document_id}/retry", status_code=202, response_model=DocumentInfo, responses=errors)
    def retry(document_id: str, request: Request):
        access = authenticate_request(identity, request)

        def run():
            version = documents.retry(access, document_id)
            document, _ = documents.document(access, document_id)
            return describe(document, version)
        return _safely(new_request_id(), run)

    @router.delete("/{document_id}", status_code=204, responses=errors)
    def delete(document_id: str, request: Request):
        access = authenticate_request(identity, request)
        result = _safely(new_request_id(), lambda: documents.delete(access, document_id))
        return result if isinstance(result, JSONResponse) else Response(status_code=204)

    app.include_router(router)


def _uploaded(result):
    return result.document, result.version, result.duplicate


def _version_info(documents: DocumentIngestionService, version) -> DocumentVersionInfo:
    job = version.job
    return DocumentVersionInfo(
        version=version.version,
        status=job.state.value,
        message=job.message,
        progress_percent=job.progress_percent,
        file_type=version.file_type,
        size_bytes=version.size_bytes,
        passage_count=version.passage_count,
        notes=list(version.notes),
        can_retry=documents.can_retry(version),
        uploaded_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


def _safely(request_id: str, action):
    """Known outcomes become safe messages; anything else is logged and hidden."""
    try:
        return action()
    except HTTPException:
        raise
    except DocumentNotFound as exc:
        return _error(404, str(exc), request_id)
    except DocumentRejected as refusal:
        logger.info("FLOWONE_DOCUMENT_REJECTED request_id=%s code=%s", request_id, refusal.code)
        return _error(_STATUS_FOR_CODE.get(refusal.code, 400), refusal.message, request_id)
    except Exception:
        logger.exception("FLOWONE_DOCUMENT_ERROR request_id=%s", request_id)
        return _error(500, "The document request could not be completed.", request_id)


def _error(status_code: int, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(status_code=status_code,
                        content={"detail": message, "request_id": request_id},
                        headers={"X-Request-ID": request_id})
