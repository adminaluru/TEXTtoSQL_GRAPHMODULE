from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class EntityMention(BaseModel):

    model_config = ConfigDict(
        extra="forbid"
    )

    concept: str
    attribute: str
    value: str


class QuestionAnalysis(BaseModel):
    """
    Structured BUSINESS interpretation of a question.

    No PostgreSQL tables, columns, joins, or SQL
    belong in this model.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    intent: Literal[
        "aggregate",
        "lookup",
        "list",
        "count",
        "unknown",
    ]

    concepts: list[str] = Field(
        default_factory=list
    )

    # --------------------------------------------------
    # What entity should the answer represent?
    #
    # Examples:
    #
    # "How much has Blue Prism paid?"
    #     result_concept = Customer
    #
    # "Show Blue Prism payments"
    #     result_concept = PaymentReceipt
    # --------------------------------------------------

    result_concept: str | None = None

    # --------------------------------------------------
    # Which business concept supplies the measure?
    #
    # Example:
    #
    # "How much has Blue Prism paid?"
    #     measure_concept = PaymentReceipt
    # --------------------------------------------------

    measure_concept: str | None = None
    measure_attribute: str | None = None
    # --------------------------------------------------
    # Explicit grouping requested by the user.
    #
    # Example:
    #
    # "Total payments by customer"
    #
    # group_by_concepts = ["Customer"]
    # --------------------------------------------------

    group_by_concepts: list[str] = Field(
        default_factory=list
    )

    entities: list[EntityMention] = Field(
        default_factory=list
    )

    aggregation: Literal[
        "SUM",
        "COUNT",
        "AVG",
        "MIN",
        "MAX",
    ] | None = None