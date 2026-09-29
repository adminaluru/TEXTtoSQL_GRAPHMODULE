from typing import Any

from pydantic import BaseModel

from ai.interfaces.chat_model import (
    ChatModel,
    ChatRequest,
    ChatResponse,
)


class MockChatModel(ChatModel):
    """
    Generic local test implementation of ChatModel.

    This class contains no FlowOne-specific business logic.

    Structured responses are injected only for testing.
    """

    def __init__(
        self,
        model_name: str = "mock-model",
        structured_response: dict[str, Any] | None = None,
    ) -> None:
        self._provider = "mock"
        self._model_name = model_name
        self._structured_response = structured_response

    def generate_text(
        self,
        request: ChatRequest,
    ) -> ChatResponse:

        if not request.messages:
            response_text = "No message provided."
        else:
            last_message = request.messages[-1]
            response_text = (
                f"Mock response to: "
                f"{last_message.content}"
            )

        return ChatResponse(
            content=response_text,
            provider=self._provider,
            model=self._model_name,
            usage=None,
            metadata={
                "mock": True,
            },
        )

    def generate_structured(
        self,
        request: ChatRequest,
        response_schema: type[BaseModel],
    ) -> BaseModel:

        if self._structured_response is None:
            raise ValueError(
                "No structured mock response "
                "has been configured."
            )

        return response_schema.model_validate(
            self._structured_response
        )

    def health_check(self) -> bool:
        return True

    def model_info(self) -> dict[str, str]:
        return {
            "provider": self._provider,
            "model": self._model_name,
        }