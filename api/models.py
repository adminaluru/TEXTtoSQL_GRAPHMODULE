from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class AskRequest(BaseModel):
    """
    HTTP request contract for POST /ask.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    question: str = Field(
        min_length=1,
        max_length=5000,
    )

    # jwt mode: binds clarification choices to this conversation.
    # Ignored in api_key mode (no verified caller to bind to).
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )


class MeResponse(BaseModel):
    """
    GET /me: who the verified caller is and which companies
    (tenants) they may work in. Everything comes from the
    verified token, never from the request.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    subject_id: str

    kind: str

    roles: list[str]

    tenants: list[str]


class ClarifyRequest(BaseModel):
    """
    HTTP request contract for POST /clarify (jwt mode).
    Identity and tenant come from the token, never from here.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    conversation_id: str = Field(min_length=1, max_length=200)

    clarification_id: str = Field(min_length=1, max_length=200)

    option_id: str = Field(min_length=1, max_length=200)


class APIErrorResponse(BaseModel):
    """
    Stable API error contract.

    detail is a generic, client-safe message.
    request_id correlates the error with server logs.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    detail: str

    request_id: str | None = None

# ==========================================================
# Documents (Step 8). Workspace and uploader come from the
# verified token; nothing here is taken from the file.
# ==========================================================

class DocumentVersionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    status: str            # queued, processing, ready, failed, deleting, deleted
    message: str | None    # safe explanation for failed or replaced versions
    progress_percent: int
    file_type: str
    size_bytes: int
    passage_count: int
    notes: list[str]       # what was skipped, e.g. hidden sheets
    can_retry: bool
    uploaded_at: str
    updated_at: str


class DocumentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    # True when a version is ready and its passages are searchable.
    searchable: bool
    active_version: int | None
    latest: DocumentVersionInfo
    # Upload only: the same file was already in this workspace.
    duplicate: bool = False


class DocumentList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentInfo]


class DocumentDetail(DocumentInfo):
    versions: list[DocumentVersionInfo]


class DocumentSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class DocumentPassage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    version: int
    # e.g. "terms.pdf · page 3"
    citation: str
    # The passage exactly as stored in the document (never reworded).
    text: str


class DocumentSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # complete, empty or clarification
    status: str
    message: str | None = None
    # The words actually searched for, so the UI can highlight them.
    terms: list[str]
    results: list[DocumentPassage]


# ==========================================================
# Saved conversations (Step 12c, decision D-012)
# ==========================================================

class ConversationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    title: str             # the first question, shortened
    created_at: str
    updated_at: str
    message_count: int


class ConversationList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Newest first; only the caller's own, in the active company.
    conversations: list[ConversationInfo]


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # question: content is the text asked
    # choice:   content is {"option_id": ..., "label": ...} (label may be null)
    # answer:   content is the answer exactly as /ask or /clarify returned it
    kind: str
    content: object
    created_at: str


class ConversationDetail(ConversationInfo):
    messages: list[ConversationMessage]
