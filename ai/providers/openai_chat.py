import logging
import os

from observability.deadline import (
    DeadlineExceeded,
    cap_timeout,
    remaining_seconds,
)
from typing import Any

from openai import OpenAI

from pydantic import BaseModel

from ai.interfaces.chat_model import (
    ChatModel,
    ChatRequest,
    ChatResponse,
)

from observability.perf import (
    PerfTrace,
)


# ==========================================================
# Performance logger
#
# No prompt text, question text, API keys, or business
# values are logged.
# ==========================================================

logger = logging.getLogger(
    "uvicorn.error"
)


class OpenAIChatModel(ChatModel):
    """
    OpenAI implementation of the provider-neutral
    ChatModel interface.

    Responsibilities:

    - translate internal ChatRequest -> OpenAI format
    - execute OpenAI Responses API calls
    - convert OpenAI responses -> internal contracts
    - produce Pydantic structured outputs

    Provider-specific OpenAI objects must NOT escape
    this adapter.

    Performance instrumentation measures:

    - provider input construction
    - OpenAI SDK/API call
    - parsed-response extraction
    - Pydantic result validation/coercion
    - token usage
    - total adapter latency

    It does NOT log:

    - API keys
    - prompts
    - user questions
    - business entity values
    """

    def __init__(
        self,
        model_name: str | None = None,
        client: OpenAI | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
    ) -> None:

        self._provider = "openai"

        self._model_name = (
            model_name
            or os.getenv(
                "OPENAI_MODEL",
                "gpt-5.6-luna",
            )
        )

        # One client is created for this adapter instance
        # and reused across requests.
        #
        # We deliberately do NOT instantiate OpenAI()
        # separately for every generate_*() call.
        #
        # An explicit timeout bounds how long a slow
        # provider can hold a worker thread; the SDK
        # default is several minutes.
        self._timeout_seconds = timeout_seconds

        self._client = (
            client
            or OpenAI(
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
        )

    # ======================================================
    # Normal text generation
    # ======================================================

    def generate_text(
        self,
        request: ChatRequest,
    ) -> ChatResponse:

        if not request.messages:

            raise ValueError(
                "ChatRequest must contain "
                "at least one message."
            )

        with self._trace(
            operation="text",
            request=request,
            schema_name=None,
        ) as trace:

            # ==================================================
            # 1. Internal request -> provider input
            # ==================================================

            with trace.stage("input_build"):

                provider_input = (
                    self._build_input(
                        request
                    )
                )

            kwargs: dict[
                str,
                Any,
            ] = {
                "model":
                    self._model_name,

                "input":
                    provider_input,
            }

            if (
                request.max_tokens
                is not None
            ):

                kwargs[
                    "max_output_tokens"
                ] = request.max_tokens

            # ==================================================
            # 2. OpenAI Responses API
            #
            # Includes SDK request handling, network round
            # trip, provider inference and deserialization.
            # ==================================================

            with trace.stage("api_call"):

                # Never longer than the time left for the whole question.
                call_timeout = cap_timeout(self._timeout_seconds)

                try:

                    response = (
                        self._client
                        .responses
                        .create(
                            **kwargs,
                            timeout=call_timeout,
                        )
                    )

                except Exception as exc:

                    remaining = remaining_seconds()
                    if remaining is not None and remaining <= 0:
                        raise DeadlineExceeded() from exc

                    raise RuntimeError(
                        "OpenAI text generation failed."
                    ) from exc

            trace.fields.update(
                self._usage_fields(
                    response
                )
            )

            # ==================================================
            # 3. Response extraction
            # ==================================================

            with trace.stage("response_extract"):

                content = (
                    response.output_text
                    or ""
                )

            if not content:

                raise RuntimeError(
                    "OpenAI returned an empty "
                    "text response."
                )

            # ==================================================
            # 4. Internal ChatResponse construction
            # ==================================================

            with trace.stage("response_build"):

                result = ChatResponse(
                    content=
                        content,

                    provider=
                        self._provider,

                    model=
                        self._model_name,

                    usage=
                        self._extract_usage(
                            response
                        ),

                    metadata={
                        "response_id":
                            getattr(
                                response,
                                "id",
                                None,
                            ),
                    },
                )

            return result

    # ======================================================
    # Structured generation
    # ======================================================

    def generate_structured(
        self,
        request: ChatRequest,
        response_schema: type[BaseModel],
    ) -> BaseModel:
        """
        Generate a response conforming to the supplied
        Pydantic schema.

        QuestionAnalyzer uses this method to produce
        QuestionAnalysis.
        """

        if not request.messages:

            raise ValueError(
                "ChatRequest must contain "
                "at least one message."
            )

        with self._trace(
            operation="structured",
            request=request,
            schema_name=response_schema.__name__,
        ) as trace:

            # ==================================================
            # 1. Internal ChatRequest -> OpenAI input
            # ==================================================

            with trace.stage("input_build"):

                provider_input = (
                    self._build_input(
                        request
                    )
                )

            kwargs: dict[
                str,
                Any,
            ] = {
                "model":
                    self._model_name,

                "input":
                    provider_input,

                "text_format":
                    response_schema,
            }

            if (
                request.max_tokens
                is not None
            ):

                kwargs[
                    "max_output_tokens"
                ] = request.max_tokens

            # ==================================================
            # 2. OpenAI structured API call
            #
            # responses.parse() includes both the remote
            # OpenAI operation and SDK structured-output
            # handling. This is the dominant latency stage.
            # ==================================================

            with trace.stage("api_parse_call"):

                # Never longer than the time left for the whole question.
                call_timeout = cap_timeout(self._timeout_seconds)

                try:

                    response = (
                        self._client
                        .responses
                        .parse(
                            **kwargs,
                            timeout=call_timeout,
                        )
                    )

                except Exception as exc:

                    remaining = remaining_seconds()
                    if remaining is not None and remaining <= 0:
                        raise DeadlineExceeded() from exc

                    raise RuntimeError(
                        "OpenAI structured generation "
                        "failed."
                    ) from exc

            trace.fields.update(
                self._usage_fields(
                    response
                )
            )

            # ==================================================
            # 3. Extract parsed SDK payload
            # ==================================================

            with trace.stage("parsed_extract"):

                parsed = (
                    self._extract_parsed_response(
                        response
                    )
                )

            if parsed is None:

                raise RuntimeError(
                    "OpenAI returned no parsed "
                    "structured output."
                )

            # ==================================================
            # 4. Pydantic coercion / validation
            #
            # Defensive validation so no provider-specific
            # object escapes this adapter.
            # ==================================================

            with trace.stage("schema_validation"):

                if isinstance(
                    parsed,
                    response_schema,
                ):

                    result = parsed

                elif isinstance(
                    parsed,
                    BaseModel,
                ):

                    result = (
                        response_schema
                        .model_validate(
                            parsed.model_dump()
                        )
                    )

                else:

                    result = (
                        response_schema
                        .model_validate(
                            parsed
                        )
                    )

            return result

    # ======================================================
    # Health
    # ======================================================

    def health_check(
        self,
    ) -> bool:
        """
        Verify authentication and model access.

        This avoids running the full Research Agent.
        """

        try:

            self._client.models.retrieve(
                self._model_name
            )

            return True

        except Exception as exc:

            print(
                "OpenAI health check failed: "
                f"{exc}"
            )

            return False

    # ======================================================
    # Model metadata
    # ======================================================

    def model_info(
        self,
    ) -> dict[
        str,
        str,
    ]:

        return {
            "provider":
                self._provider,

            "model":
                self._model_name,
        }

    # ======================================================
    # Request translation
    # ======================================================

    @staticmethod
    def _build_input(
        request: ChatRequest,
    ) -> list[
        dict[
            str,
            str,
        ]
    ]:
        """
        Translate provider-neutral ChatMessage objects
        into OpenAI Responses API input messages.
        """

        return [
            {
                "role":
                    message.role,

                "content":
                    message.content,
            }

            for message
            in request.messages
        ]

    # ======================================================
    # Structured-response extraction
    # ======================================================

    @staticmethod
    def _extract_parsed_response(
        response: Any,
    ) -> Any | None:
        """
        Extract the Pydantic-parsed payload returned by
        responses.parse().
        """

        for output in getattr(
            response,
            "output",
            [],
        ):

            if (
                getattr(
                    output,
                    "type",
                    None,
                )
                != "message"
            ):

                continue

            for content in getattr(
                output,
                "content",
                [],
            ):

                if (
                    getattr(
                        content,
                        "type",
                        None,
                    )
                    != "output_text"
                ):

                    continue

                parsed = getattr(
                    content,
                    "parsed",
                    None,
                )

                if parsed is not None:

                    return parsed

        return None

    # ======================================================
    # Usage conversion
    # ======================================================

    @staticmethod
    def _extract_usage(
        response: Any,
    ) -> dict[
        str,
        Any,
    ] | None:

        usage = getattr(
            response,
            "usage",
            None,
        )

        if usage is None:

            return None

        input_details = getattr(
            usage,
            "input_tokens_details",
            None,
        )

        output_details = getattr(
            usage,
            "output_tokens_details",
            None,
        )

        result: dict[
            str,
            Any,
        ] = {
            "input_tokens":
                getattr(
                    usage,
                    "input_tokens",
                    None,
                ),

            "output_tokens":
                getattr(
                    usage,
                    "output_tokens",
                    None,
                ),

            "total_tokens":
                getattr(
                    usage,
                    "total_tokens",
                    None,
                ),
        }

        if input_details is not None:

            result[
                "cached_input_tokens"
            ] = getattr(
                input_details,
                "cached_tokens",
                None,
            )

        if output_details is not None:

            result[
                "reasoning_tokens"
            ] = getattr(
                output_details,
                "reasoning_tokens",
                None,
            )

        return result

    # ======================================================
    # Request-size metrics
    # ======================================================

    @staticmethod
    def _request_metrics(
        request: ChatRequest,
    ) -> dict[
        str,
        int,
    ]:

        message_count = (
            len(
                request.messages
            )
        )

        total_chars = sum(
            len(
                message.content
                or ""
            )

            for message
            in request.messages
        )

        system_chars = sum(
            len(
                message.content
                or ""
            )

            for message
            in request.messages

            if message.role
            == "system"
        )

        user_chars = sum(
            len(
                message.content
                or ""
            )

            for message
            in request.messages

            if message.role
            == "user"
        )

        return {
            "message_count":
                message_count,

            "input_chars":
                total_chars,

            "system_chars":
                system_chars,

            "user_chars":
                user_chars,
        }

    # ======================================================
    # Structured performance logging
    # ======================================================

    PERF_STAGES = [
        "input_build",
        "api_call",
        "api_parse_call",
        "response_extract",
        "parsed_extract",
        "schema_validation",
        "response_build",
    ]

    def _trace(
        self,
        operation: str,
        request: ChatRequest,
        schema_name: str | None,
    ) -> PerfTrace:

        request_metrics = (
            self._request_metrics(
                request
            )
        )

        return PerfTrace(
            event="FLOWONE_OPENAI_PERF",
            id_field="call_id",
            stage_order=self.PERF_STAGES,
            logger=logger,
            fields={
                "operation": operation,
                "model": self._model_name,
                "schema": schema_name,
                "messages": request_metrics["message_count"],
                "input_chars": request_metrics["input_chars"],
                "system_chars": request_metrics["system_chars"],
                "user_chars": request_metrics["user_chars"],
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "cached_input_tokens": None,
                "reasoning_tokens": None,
            },
        )

    @classmethod
    def _usage_fields(
        cls,
        response: Any,
    ) -> dict[
        str,
        Any,
    ]:

        usage = (
            cls._extract_usage(
                response
            )
            or {}
        )

        return {
            name: usage.get(name)

            for name in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cached_input_tokens",
                "reasoning_tokens",
            )
        }
