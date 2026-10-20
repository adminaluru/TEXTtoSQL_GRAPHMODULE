"""HTTP routes for saved conversations (Step 12c, decision D-012). jwt mode only.

    GET    /conversations          the caller's conversations in the active company, newest first
    GET    /conversations/{id}     one conversation with its messages
    DELETE /conversations/{id}     delete it and its messages -> 204

Owner = the verified caller and the active company from the token. Another
user's or another company's conversation answers 404, exactly like a
missing one. Conversations are saved by /ask and /clarify when the request
names a conversation_id.
"""
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response

from adapters.conversation_store import ConversationSummary, SqliteConversationStore
from api.identity import authenticate_request
from api.models import APIErrorResponse, ConversationDetail, ConversationInfo, ConversationList, ConversationMessage
from trusted_identity import IdentityVerifier

NOT_FOUND = "Conversation not found."


def add_conversation_routes(app: FastAPI, identity: IdentityVerifier, store: SqliteConversationStore) -> None:
    router = APIRouter(prefix="/conversations", tags=["conversations"])
    errors = {status: {"model": APIErrorResponse} for status in (401, 403, 404)}

    @router.get("", response_model=ConversationList, responses=errors)
    def list_conversations(http_request: Request):
        access = authenticate_request(identity, http_request)
        return ConversationList(conversations=[
            _info(s) for s in store.recent(access.tenant_id, access.subject_id)])

    @router.get("/{conversation_id}", response_model=ConversationDetail, responses=errors)
    def get_conversation(conversation_id: str, http_request: Request):
        access = authenticate_request(identity, http_request)
        summary = store.summary(access.tenant_id, access.subject_id, conversation_id)
        messages = store.messages(access.tenant_id, access.subject_id, conversation_id)
        if summary is None or messages is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return ConversationDetail(
            **_info(summary).model_dump(),
            messages=[ConversationMessage(kind=m.kind, content=m.content, created_at=m.created_at.isoformat())
                      for m in messages])

    @router.delete("/{conversation_id}", status_code=204, responses=errors)
    def delete_conversation(conversation_id: str, http_request: Request):
        access = authenticate_request(identity, http_request)
        if not store.delete(access.tenant_id, access.subject_id, conversation_id):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return Response(status_code=204)

    app.include_router(router)


def _info(summary: ConversationSummary) -> ConversationInfo:
    return ConversationInfo(conversation_id=summary.conversation_id, title=summary.title,
                            created_at=summary.created_at.isoformat(), updated_at=summary.updated_at.isoformat(),
                            message_count=summary.message_count)
