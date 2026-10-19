from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    model_serializer,
)


class SourcePassage(BaseModel):
    """A document passage quoted exactly as stored, with where it comes from."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    version: int
    # e.g. "terms.pdf · page 3"
    citation: str
    text: str


class AnswerResponse(BaseModel):
    """
    Final application-facing answer.

    The textual answer is accompanied by structured
    data so API/UI consumers do not need to parse text.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    answer: str

    # "clarification": the agent could not answer safely
    # and `answer` asks the user to rephrase.
    result_type: Literal[
        "empty",
        "scalar",
        "tabular",
        "clarification",
        # Step 11: the answer is quoted document passages only.
        "documents",
    ]

    row_count: int

    data: Any | None = None

    warnings: list[str] = Field(
        default_factory=list
    )

    # Step 11: which sources answered ("database", "documents", "both"),
    # and the document passages used. Left out of the JSON when not set,
    # so the plain database answer keeps exactly its original shape.
    route: str | None = None

    passages: list[SourcePassage] | None = None

    # Why a clarification was returned (e.g. "clarification_entity",
    # "clarification_unsupported"). Internal: never serialized, so the
    # /ask JSON contract is unchanged; adapters map it to their status.
    _outcome: str | None = PrivateAttr(default=None)

    # How the question was routed and timed (internal; for logs and the
    # routing benchmark, never serialized).
    _route_trace: dict | None = PrivateAttr(default=None)

    @model_serializer(mode="wrap")
    def _omit_unset_routing(self, handler):
        data = handler(self)
        for name in ("route", "passages"):
            if data.get(name) is None:
                data.pop(name, None)
        return data

    @property
    def outcome(self) -> str | None:
        return self._outcome