from dataclasses import dataclass, field

from ai.interfaces.chat_model import (
    ChatMessage,
)

from answering.models import (
    AnswerResponse,
)

from nlu.models import (
    QuestionAnalysis,
)

from planning.fanout_guard import (
    FanOutViolation,
)

from planning.graph_models import (
    GraphPlan,
)


# ==========================================================
# Error taxonomy
#
# ValueError raised while INTERPRETING a question (NLU,
# normalization, planning, column resolution, plan build)
# is treated as repairable: the LLM may have chosen the
# wrong concept, attribute or structure.
#
# UnsupportedQuestionError marks capability limits. These
# are never retried: the only way an LLM could "fix" them is
# by answering a different question than the one asked.
# ==========================================================

class UnsupportedQuestionError(ValueError):
    """The question is understood but not supported yet."""


# Rejection text sent back to the LLM is truncated so a
# long validation dump cannot dominate the prompt.
MAX_REASON_CHARS = 600


@dataclass
class RepairState:
    """
    What is known about the current attempt, so a failure
    can be fed back to the LLM and explained to the user.
    """

    raw_analysis: QuestionAnalysis | None = None

    graph_plan: GraphPlan | None = None

    failures: list[str] = field(
        default_factory=list
    )

    # A user-facing question from the latest failure (e.g. which of
    # several attributes was meant), shown if repair does not resolve it.
    clarification: str | None = None


# ==========================================================
# LLM feedback
# ==========================================================

def build_repair_messages(
    state: RepairState,
    reason: str,
) -> list[ChatMessage]:
    """
    Conversation turns appended after the original question
    on the next attempt.

    The model sees its previous structured answer (when it
    produced one) and the deterministic rejection. It is
    told to keep the user's meaning and to answer "unknown"
    rather than reinterpret the question.
    """

    messages: list[ChatMessage] = []

    if state.raw_analysis is not None:

        messages.append(
            ChatMessage(
                role="assistant",
                content=state.raw_analysis.model_dump_json(),
            )
        )

    messages.append(
        ChatMessage(
            role="user",
            content=(
                "Your previous QuestionAnalysis could not be "
                "used. Rejection reason:\n"
                f"{reason[:MAX_REASON_CHARS]}\n\n"
                "Return a corrected QuestionAnalysis for the "
                "ORIGINAL question.\n"
                "- Use only concepts from AVAILABLE BUSINESS "
                "CONCEPTS.\n"
                "- Use business-level attribute names, never "
                "table or column names.\n"
                "- Do not change what the user asked for: do "
                "not drop filters, measures or conditions to "
                "make the query fit.\n"
                "- If the question cannot be represented "
                "faithfully, set intent to \"unknown\"."
            ),
        )
    )

    return messages


# ==========================================================
# User-facing clarifications
#
# Deterministic text built from live ontology names only.
# No internal table/column names or stack details.
# ==========================================================

UNSUPPORTED_MESSAGE = (
    "I can't answer this kind of question yet. I can answer "
    "a single total, average, count, minimum or maximum, or "
    "list records, with simple filters. Could you rephrase "
    "your question in one of those forms?"
)

REPAIR_WARNING = (
    "The question was automatically re-interpreted before "
    "it could be answered. Please check that the answer "
    "matches what you asked."
)


def clarification(
    message: str,
) -> AnswerResponse:

    return AnswerResponse(
        answer=message,
        result_type="clarification",
        row_count=0,
        data=None,
        warnings=[],
    )


def unresolved_message(
    state: RepairState,
) -> str:
    """
    Clarification after repair attempts are exhausted.
    """

    if state.clarification:
        return state.clarification

    concepts = (
        state.raw_analysis.concepts
        if state.raw_analysis is not None
        else []
    )

    understood = ""

    if concepts:

        understood = (
            " I understood it as involving: "
            + ", ".join(concepts)
            + "."
        )

    return (
        "I couldn't match your question to the available "
        "business data."
        + understood
        + " Could you rephrase it, naming the business "
        "records involved and what you want to know about "
        "them (for example a total, a count or a list)?"
    )


def fanout_message(
    violations: list[FanOutViolation],
    graph_plan: GraphPlan,
) -> str:
    """
    Explain a blocked double-counting aggregation in
    business terms.
    """

    concept_by_table = {
        table.key: table.business_concept or table.key

        for table
        in graph_plan.tables
    }

    violation = violations[0]

    measure = concept_by_table.get(
        violation.measure_table,
        violation.measure_table,
    )

    other = concept_by_table.get(
        violation.to_table,
        violation.to_table,
    )

    return (
        f"Answering this would count some {measure} records "
        f"more than once, because one {measure} can have "
        f"several {other} records. Could you ask about the "
        f"{measure} records without the {other} condition, "
        f"or ask about the {other} records directly?"
    )
