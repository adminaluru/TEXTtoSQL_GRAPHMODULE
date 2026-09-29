from ai.interfaces.chat_model import ChatModel
from ai.providers.mock_chat import MockChatModel

from config import settings


class UnsupportedLLMProviderError(ValueError):
    """
    Raised when the configured LLM provider
    has no registered adapter.
    """


def create_chat_model() -> ChatModel:
    """
    Create the configured ChatModel implementation.

    This function is the ONLY place responsible for
    selecting the provider implementation.

    Core application components should never contain
    provider-selection logic.
    """

    provider = (
        settings.llm_provider
        or "mock"
    ).strip().lower()

    model = (
        settings.llm_model
        or "mock-model"
    )

    if provider == "mock":

        return MockChatModel(
            model_name=model,
        )

    raise UnsupportedLLMProviderError(
        f"Unsupported LLM provider: {provider}"
    )