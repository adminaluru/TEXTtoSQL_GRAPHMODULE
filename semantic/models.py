from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


# ==========================================================
# Stable semantic reason codes
# ==========================================================

SemanticReasonCode = Literal[
    "UNKNOWN_CONCEPT",
    "RESULT_CONCEPT_NOT_IN_QUERY",
    "MEASURE_CONCEPT_NOT_IN_QUERY",
    "FILTER_CONCEPT_NOT_IN_QUERY",
    "GROUP_BY_CONCEPT_NOT_IN_QUERY",
    "EXISTENCE_CONCEPT_NOT_IN_QUERY",
    "MISSING_AGGREGATE_TARGET",
    "MISSING_ATTRIBUTE_TARGET",
    "UNSUPPORTED_QUERY_TYPE",
    "UNSUPPORTED_GROUP_BY",
    "UNSUPPORTED_ATTRIBUTE_COUNT",
    "UNSUPPORTED_DISTINCT_MODE",
    "SEMANTIC_CONTRADICTION",
]


# ==========================================================
# Aggregation
# ==========================================================

class AggregationIntent(BaseModel):
    """
    Canonical business-level aggregation.

    Contains NO:
    - PostgreSQL tables
    - physical columns
    - SQL
    - join information
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    function: Literal[
        "COUNT",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
    ]

    target_kind: Literal[
        "entity",
        "attribute",
    ]

    concept: str

    attribute: str | None = None

    # This means the USER explicitly requested distinctness.
    #
    # It is NOT the same as physical deduplication caused
    # by one-to-many/many-to-many joins.
    distinct_requested: bool = False

    @model_validator(
        mode="after"
    )
    def validate_target(
        self,
    ) -> "AggregationIntent":

        if (
            self.target_kind == "entity"
            and self.attribute is not None
        ):

            raise ValueError(
                "Entity aggregation cannot contain "
                "an attribute target."
            )

        if (
            self.target_kind == "attribute"
            and not self.attribute
        ):

            raise ValueError(
                "Attribute aggregation requires "
                "an attribute target."
            )

        if (
            self.function
            in {
                "SUM",
                "AVG",
                "MIN",
                "MAX",
            }
            and self.target_kind
            != "attribute"
        ):

            raise ValueError(
                f"{self.function} requires an "
                "attribute target."
            )

        return self


# ==========================================================
# Grouping
# ==========================================================

class GroupingIntent(BaseModel):
    """
    Canonical business-level grouping intent.

    Examples:

        "payment amount by customer"

        GroupingIntent(
            target_kind="entity",
            concept="Customer",
            attribute=None,
        )

    Future example:

        "payment amount by payment method"

        GroupingIntent(
            target_kind="attribute",
            concept="PaymentReceipt",
            attribute="payment method",
        )

    This model deliberately contains NO physical
    database decisions.

    It does NOT specify:

    - PostgreSQL table
    - primary key
    - display column
    - GROUP BY SQL expression
    - SELECT projection
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    target_kind: Literal[
        "entity",
        "attribute",
    ]

    concept: str

    attribute: str | None = None

    @model_validator(
        mode="after"
    )
    def validate_target(
        self,
    ) -> "GroupingIntent":

        if (
            self.target_kind == "entity"
            and self.attribute is not None
        ):

            raise ValueError(
                "Entity grouping cannot contain "
                "an attribute target."
            )

        if (
            self.target_kind == "attribute"
            and not self.attribute
        ):

            raise ValueError(
                "Attribute grouping requires "
                "an attribute target."
            )

        return self


# ==========================================================
# Existence
# ==========================================================

class ExistenceIntent(BaseModel):
    """
    Canonical business-level existence requirement.

    Example:

        "Which invoices have broken promises to pay?"

        ExistenceIntent(
            concept="BrokenPromiseToPay"
        )

    This expresses only BUSINESS meaning:

        a related entity of this concept must exist

    It deliberately contains NO:

    - PostgreSQL tables
    - physical join paths
    - SQL EXISTS syntax
    - join columns
    - aliases
    - correlation logic

    Physical existence-path planning happens later.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    concept: str


# ==========================================================
# Filters
# ==========================================================

class SemanticFilter(BaseModel):
    """
    Business-level predicate.

    Physical column resolution happens later.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    concept: str

    attribute: str

    operator: Literal[
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "IN",
        "NOT_IN",
        "IS_NULL",
        "IS_NOT_NULL",
    ]

    value: Any | None = None


# ==========================================================
# Canonical Semantic Query
# ==========================================================

class SemanticQuery(BaseModel):
    """
    Canonical, validated BUSINESS meaning of a question.

    This is the contract between probabilistic NLU and
    deterministic planning.

    It contains NO physical database decisions.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    semantic_version: Literal[
        "1.0"
    ] = "1.0"

    query_type: Literal[
        "aggregate",
        "list",
        "lookup",
    ]

    concepts: list[str]

    result_concept: str | None = None

    aggregations: list[
        AggregationIntent
    ] = Field(
        default_factory=list
    )

    # ======================================================
    # Canonical grouping semantics
    #
    # Empty for scalar aggregates.
    #
    # Example:
    #
    #     What is total payment amount by customer?
    #
    # becomes:
    #
    #     GroupingIntent(
    #         target_kind="entity",
    #         concept="Customer",
    #         attribute=None,
    #     )
    #
    # Physical grouping columns are resolved later.
    # ======================================================

    groupings: list[
        GroupingIntent
    ] = Field(
        default_factory=list
    )

    # ======================================================
    # Canonical existence semantics
    # ======================================================

    existence_conditions: list[
        ExistenceIntent
    ] = Field(
        default_factory=list
    )

    filters: list[
        SemanticFilter
    ] = Field(
        default_factory=list
    )


# ==========================================================
# Normalization outcome
# ==========================================================

class SemanticNormalizationResult(BaseModel):
    """
    Explicit result of the semantic trust boundary.

    We do not use exceptions for normal unsupported/
    clarification outcomes.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    status: Literal[
        "ready",
        "clarification_required",
        "unsupported",
        "invalid",
    ]

    query: SemanticQuery | None = None

    reason_code: (
        SemanticReasonCode
        | None
    ) = None

    message: str | None = None

    @model_validator(
        mode="after"
    )
    def validate_outcome(
        self,
    ) -> "SemanticNormalizationResult":

        if self.status == "ready":

            if self.query is None:

                raise ValueError(
                    "Ready semantic result "
                    "requires a query."
                )

            if self.reason_code is not None:

                raise ValueError(
                    "Ready semantic result cannot "
                    "contain a reason code."
                )

        else:

            if self.query is not None:

                raise ValueError(
                    "Non-ready semantic result "
                    "cannot contain an executable "
                    "semantic query."
                )

            if self.reason_code is None:

                raise ValueError(
                    "Non-ready semantic result "
                    "requires a reason code."
                )

        return self