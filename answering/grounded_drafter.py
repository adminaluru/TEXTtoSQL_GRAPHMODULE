"""Connect the grounded_answers package to the application's chat model (Step 12a, D-012).

Document passages are sent to the AI provider here. D-012 allows that for
synthetic development data only; the composition root refuses to enable it
when APP_ENV=production.
"""
from grounded_answers import Draft

from ai.interfaces.chat_model import ChatMessage, ChatModel, ChatRequest

# Four short sentences with their passage numbers fit comfortably.
MAX_DRAFT_TOKENS = 600


def chat_drafter(chat_model: ChatModel):
    def draft(instructions: str, payload: str) -> Draft:
        request = ChatRequest(
            messages=[ChatMessage(role="system", content=instructions),
                      ChatMessage(role="user", content=payload)],
            temperature=0.0,
            max_tokens=MAX_DRAFT_TOKENS,
        )
        reply = chat_model.generate_structured(request=request, response_schema=Draft)
        return Draft.model_validate(reply.model_dump() if hasattr(reply, "model_dump") else reply)

    return draft
