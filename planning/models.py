from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class QueryFilter(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    table: str
    column: str

    operator: Literal[
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "IN",
        "LIKE",
        "ILIKE",
    ]

    value: Any

    # A company (tenant) restriction added by access policy, not by the
    # question. Policies may move it into a LEFT JOIN's ON clause, never drop it.
    tenant_scope: bool = False


# ==========================================================
# Projection
# ==========================================================

class QueryProjection(BaseModel):
    """
    One physical non-aggregate SELECT expression.

    Example:

        public.erp_customer_details.name
            AS customer_name

    This model contains only resolved physical
    information.

    It does NOT perform:
    - business reasoning
    - ontology lookup
    - column discovery
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    table: str

    column: str

    alias: str


# ==========================================================
# Grouping
# ==========================================================

class QueryGrouping(BaseModel):
    """
    One resolved physical GROUP BY column.

    Entity-level grouping may require multiple physical
    columns.

    Example:

        Customer entity grouping

    can become:

        GROUP BY customer.id,
                 customer.name

    The identity column prevents distinct entities with
    the same display value from being collapsed.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    table: str

    column: str


class QueryAggregation(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    function: Literal[
        "SUM",
        "COUNT",
        "AVG",
        "MIN",
        "MAX",
    ]

    table: str
    column: str
    alias: str

    # COALESCE(<aggregate>, 0) for groups without records (D-005).
    # Only meaningful for SUM and COUNT; the compiler rejects others.
    empty_as_zero: bool = False

    # Count of measure records whose value is NULL (D-006), selected as
    # COUNT(table.missing_key_column) FILTER (WHERE table.column IS NULL).
    missing_key_column: str | None = None

    missing_alias: str | None = None


class QueryJoin(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    left_table: str
    left_column: str

    right_table: str
    right_column: str

    join_type: Literal[
        "INNER",
        "LEFT",
    ] = "INNER"

    validation_status: str | None = None

    # Equality conditions on the joined table, compiled into the ON clause
    # as bound parameters. Used for company restrictions on LEFT JOINs so
    # rows from other companies are excluded without dropping empty groups.
    on_filters: list[QueryFilter] = Field(
        default_factory=list
    )


# ==========================================================
# Existence / Semi-join Condition
# ==========================================================

class QueryExistence(BaseModel):
    """
    One fully resolved physical existence condition.

    This model represents business semantics such as:

        "has broken promises"
        "has invoices with claims"

    without turning the qualifying relationship into a
    result-producing JOIN.

    The condition is anchored to the outer result table and
    contains the physical subquery path needed to prove that
    at least one related row exists.

    It contains only resolved physical database information.

    It does NOT:
    - perform business reasoning
    - discover paths
    - resolve concepts
    - generate SQL
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    anchor_table: str

    tables: list[str] = Field(
        default_factory=list
    )

    joins: list[
        QueryJoin
    ] = Field(
        default_factory=list
    )

    filters: list[
        QueryFilter
    ] = Field(
        default_factory=list
    )


class QueryPlan(BaseModel):
    """
    Fully resolved database execution plan.

    This contains physical PostgreSQL information,
    but still contains NO SQL string.

    QueryPlan may represent:

    - scalar aggregates
    - grouped aggregates
    - projection/list queries
    - correlated existence / semi-join conditions

    All semantic reasoning must already be complete
    before this model is created.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    intent: str

    grain: list[str] = Field(
        default_factory=list
    )

    tables: list[str] = Field(
        default_factory=list
    )

    joins: list[
        QueryJoin
    ] = Field(
        default_factory=list
    )

    filters: list[
        QueryFilter
    ] = Field(
        default_factory=list
    )

    # ======================================================
    # Non-aggregate SELECT expressions
    #
    # Example:
    #
    #     Customer.name AS customer_name
    #
    # Empty for existing scalar aggregate queries.
    # ======================================================

    projections: list[
        QueryProjection
    ] = Field(
        default_factory=list
    )

    aggregations: list[
        QueryAggregation
    ] = Field(
        default_factory=list
    )

    # ======================================================
    # Physical GROUP BY columns
    #
    # Example entity grouping:
    #
    #     Customer.id
    #     Customer.name
    #
    # Empty for existing scalar aggregate queries.
    # ======================================================

    groupings: list[
        QueryGrouping
    ] = Field(
        default_factory=list
    )
    # ======================================================
    # Correlated existence / semi-join conditions
    #
    # Each entry becomes one deterministic EXISTS condition
    # during SQL compilation.
    #
    # Multiple entries are ANDed by the compiler.
    #
    # Empty for queries with no existence semantics.
    # ======================================================

    existence_conditions: list[
        QueryExistence
    ] = Field(
        default_factory=list
    )


    limit: int | None = Field(
        default=None,
        ge=1,
        le=1000,
    )

    # Alias of the currency projection added by CurrencySplitter
    # (decision D-004). When set, monetary aggregates are grouped
    # by currency and never combined across currencies.
    currency_alias: str | None = None

    # User-facing notes about what the result does not cover, carried
    # with the plan so every execution path reports them.
    result_notes: list[str] = Field(
        default_factory=list
    )