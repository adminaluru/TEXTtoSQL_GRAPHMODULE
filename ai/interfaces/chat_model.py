from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """
    Provider-neutral chat message.

    Every LLM adapter must translate this internal
    representation into its provider-specific format.
    """

    role: str
    content: str


class ChatRequest(BaseModel):
    """
    Provider-neutral request sent to a chat model.
    """

    messages: list[ChatMessage]

    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
    )

    max_tokens: int | None = None


class ChatResponse(BaseModel):
    """
    Provider-neutral response returned by a chat model.

    Provider-specific response objects must never leak
    into the rest of the Research Agent.
    """

    content: str

    provider: str
    model: str

    usage: dict[str, Any] | None = None

    metadata: dict[str, Any] | None = None


class ChatModel(ABC):
    """
    Provider-agnostic interface for generative chat models.

    Application services depend on this interface,
    never directly on OpenAI, Bedrock, Azure OpenAI,
    Anthropic, etc.
    """

    @abstractmethod
    def generate_text(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        """
        Generate a normal text response.
        """
        raise NotImplementedError

    @abstractmethod
    def generate_structured(
        self,
        request: ChatRequest,
        response_schema: type[BaseModel],
    ) -> BaseModel:
        """
        Generate structured output matching a
        Pydantic response schema.

        This will later be especially important for
        QuestionAnalyzer and QueryPlan generation.
        """
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check whether the configured model/provider
        can be reached successfully.
        """
        raise NotImplementedError

    @abstractmethod
    def model_info(self) -> dict[str, str]:
        """
        Return provider/model metadata.
        """
        raise NotImplementedError