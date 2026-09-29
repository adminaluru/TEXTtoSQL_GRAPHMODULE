import logging
import os
import time
import uuid

from typing import Any

from openai import OpenAI

from pydantic import BaseModel

from ai.interfaces.chat_model import (
    ChatModel,
    ChatRequest,
    ChatResponse,
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
        self._client = (
            client
            or OpenAI()
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

        call_id = (
            uuid.uuid4().hex[:12]
        )

        total_started = (
            time.perf_counter()
        )

        timings: dict[
            str,
            float,
        ] = {}

        status = "failed"
        failed_stage = "startup"

        response = None

        try:

            # ==================================================
            # 1. Internal request -> provider input
            # ==================================================

            failed_stage = (
                "input_build"
            )

            started = (
                time.perf_counter()
            )

            provider_input = (
                self._build_input(
                    request
                )
            )

            timings[
                "input_build"
            ] = self._elapsed_ms(
                started
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
            # This timer includes:
            #
            # - SDK request handling
            # - network round trip
            # - provider processing/inference
            # - response deserialization
            #
            # It does NOT let us distinguish those pieces
            # individually.
            # ==================================================

            failed_stage = (
                "api_call"
            )

            started = (
                time.perf_counter()
            )

            try:

                response = (
                    self._client
                    .responses
                    .create(
                        **kwargs
                    )
                )

            except Exception as exc:

                raise RuntimeError(
                    "OpenAI text generation failed."
                ) from exc

            timings[
                "api_call"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 3. Response extraction
            # ==================================================

            failed_stage = (
                "response_extract"
            )

            started = (
                time.perf_counter()
            )

            content = (
                response.output_text
                or ""
            )

            timings[
                "response_extract"
            ] = self._elapsed_ms(
                started
            )

            if not content:

                raise RuntimeError(
                    "OpenAI returned an empty "
                    "text response."
                )

            # ==================================================
            # 4. Internal ChatResponse construction
            # ==================================================

            failed_stage = (
                "response_build"
            )

            started = (
                time.perf_counter()
            )

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

            timings[
                "response_build"
            ] = self._elapsed_ms(
                started
            )

            status = "success"

            return result

        finally:

            timings[
                "total"
            ] = self._elapsed_ms(
                total_started
            )

            self._log_performance(
                call_id=
                    call_id,

                operation=
                    "text",

                status=
                    status,

                failed_stage=(
                    None
                    if status == "success"
                    else failed_stage
                ),

                request=
                    request,

                response=
                    response,

                schema_name=
                    None,

                timings=
                    timings,
            )

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

        call_id = (
            uuid.uuid4().hex[:12]
        )

        total_started = (
            time.perf_counter()
        )

        timings: dict[
            str,
            float,
        ] = {}

        status = "failed"
        failed_stage = "startup"

        response = None

        try:

            # ==================================================
            # 1. Internal ChatRequest -> OpenAI input
            # ==================================================

            failed_stage = (
                "input_build"
            )

            started = (
                time.perf_counter()
            )

            provider_input = (
                self._build_input(
                    request
                )
            )

            timings[
                "input_build"
            ] = self._elapsed_ms(
                started
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
            # IMPORTANT:
            #
            # responses.parse() includes both the remote
            # OpenAI operation and SDK structured-output
            # handling.
            #
            # This is the stage we expect to explain most
            # of the current 1.5–6.6 second latency.
            # ==================================================

            failed_stage = (
                "api_parse_call"
            )

            started = (
                time.perf_counter()
            )

            try:

                response = (
                    self._client
                    .responses
                    .parse(
                        **kwargs
                    )
                )

            except Exception as exc:

                raise RuntimeError(
                    "OpenAI structured generation "
                    "failed."
                ) from exc

            timings[
                "api_parse_call"
            ] = self._elapsed_ms(
                started
            )

            # ==================================================
            # 3. Extract parsed SDK payload
            # ==================================================

            failed_stage = (
                "parsed_extract"
            )

            started = (
                time.perf_counter()
            )

            parsed = (
                self._extract_parsed_response(
                    response
                )
            )

            timings[
                "parsed_extract"
            ] = self._elapsed_ms(
                started
            )

            if parsed is None:

                raise RuntimeError(
                    "OpenAI returned no parsed "
                    "structured output."
                )

            # ==================================================
            # 4. Pydantic coercion / validation
            #
            # In the normal path responses.parse() may
            # already return the target Pydantic model.
            #
            # We still retain defensive validation so no
            # provider-specific object escapes this adapter.
            # ==================================================

            failed_stage = (
                "schema_validation"
            )

            started = (
                time.perf_counter()
            )

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

            timings[
                "schema_validation"
            ] = self._elapsed_ms(
                started
            )

            status = "success"

            return result

        finally:

            timings[
                "total"
            ] = self._elapsed_ms(
                total_started
            )

            self._log_performance(
                call_id=
                    call_id,

                operation=
                    "structured",

                status=
                    status,

                failed_stage=(
                    None
                    if status == "success"
                    else failed_stage
                ),

                request=
                    request,

                response=
                    response,

                schema_name=
                    response_schema.__name__,

                timings=
                    timings,
            )

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
    # Timing
    # ======================================================

    @staticmethod
    def _elapsed_ms(
        started: float,
    ) -> float:

        return round(
            (
                time.perf_counter()
                - started
            )
            * 1000,
            2,
        )

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

    def _log_performance(
        self,
        call_id: str,
        operation: str,
        status: str,
        failed_stage: str | None,
        request: ChatRequest,
        response: Any | None,
        schema_name: str | None,
        timings: dict[
            str,
            float,
        ],
    ) -> None:

        request_metrics = (
            self._request_metrics(
                request
            )
        )

        usage = (
            self._extract_usage(
                response
            )

            if response
            is not None

            else None
        )

        input_tokens = (
            usage.get(
                "input_tokens"
            )

            if usage

            else None
        )

        output_tokens = (
            usage.get(
                "output_tokens"
            )

            if usage

            else None
        )

        total_tokens = (
            usage.get(
                "total_tokens"
            )

            if usage

            else None
        )

        cached_input_tokens = (
            usage.get(
                "cached_input_tokens"
            )

            if usage

            else None
        )

        reasoning_tokens = (
            usage.get(
                "reasoning_tokens"
            )

            if usage

            else None
        )

        ordered_stages = [
            "input_build",
            "api_call",
            "api_parse_call",
            "response_extract",
            "parsed_extract",
            "schema_validation",
            "response_build",
            "total",
        ]

        timing_text = " ".join(
            (
                f"{stage}_ms="
                f"{timings[stage]:.2f}"
            )

            for stage
            in ordered_stages

            if stage in timings
        )

        logger.info(
            "FLOWONE_OPENAI_PERF "
            "call_id=%s "
            "operation=%s "
            "status=%s "
            "failed_stage=%s "
            "model=%s "
            "schema=%s "
            "messages=%s "
            "input_chars=%s "
            "system_chars=%s "
            "user_chars=%s "
            "input_tokens=%s "
            "output_tokens=%s "
            "total_tokens=%s "
            "cached_input_tokens=%s "
            "reasoning_tokens=%s "
            "%s",
            call_id,
            operation,
            status,
            failed_stage or "-",
            self._model_name,
            schema_name or "-",
            request_metrics[
                "message_count"
            ],
            request_metrics[
                "input_chars"
            ],
            request_metrics[
                "system_chars"
            ],
            request_metrics[
                "user_chars"
            ],
            (
                input_tokens
                if input_tokens is not None
                else "-"
            ),
            (
                output_tokens
                if output_tokens is not None
                else "-"
            ),
            (
                total_tokens
                if total_tokens is not None
                else "-"
            ),
            (
                cached_input_tokens
                if cached_input_tokens is not None
                else "-"
            ),
            (
                reasoning_tokens
                if reasoning_tokens is not None
                else "-"
            ),
            timing_text,
        )