from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class ResolvedFilterColumn(BaseModel):
    """
    Physical PostgreSQL column resolved from a
    business-level filter/entity attribute.

    Example:

    Customer.name = Blue Prism

    becomes:

    public.erp_customer_details.name = Blue Prism
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    concept: str

    business_attribute: str

    table_key: str

    column_key: str

    column_name: str

    operator: str = "="

    # Text, number, or a datetime bound for date periods (D-007).
    value: Any

    # User-facing statement of how the condition was applied,
    # e.g. the exact period a date condition covers.
    note: str | None = None

    score: int


class ResolvedMeasureColumn(BaseModel):
    """
    Physical PostgreSQL column resolved from a
    business-level measure.

    Example:

    PaymentReceipt
    payment amount

    becomes:

    public.erp_payment_receipt.payment_amount
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    concept: str

    business_attribute: str

    table_key: str

    column_key: str

    column_name: str

    data_type: str | None = None

    category: str | None = None

    score: int


# ==========================================================
# Default LIST Projection
# ==========================================================

class ResolvedProjectionColumn(BaseModel):
    """
    One ontology-approved physical column for a bounded
    default entity projection.

    Example:

        result_concept = Invoice

        ontology:
            invoice_num
                projection_role = "default"
                projection_order = 1

        resolves to:

            public.erp_invoice_details.invoice_num

    No concept-specific mappings exist in this model.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    concept: str

    table_key: str

    column_key: str

    column_name: str

    projection_order: int

    semantic_role: str | None = None


# ==========================================================
# Entity Grouping
# ==========================================================

class ResolvedEntityGrouping(BaseModel):
    """
    Physical representation of one canonical
    entity-level grouping.

    Example semantic intent:

        GROUP BY Customer entity

    Ontology-driven physical resolution:

        Customer
            ↓
        public.erp_customer_details

        identity:
            is_primary_key = True
            ↓
            id

        display:
            semantic_role = "display_name"
            ↓
            name

    Both columns are deliberately retained.

    Why?

    Grouping only by a display name can incorrectly merge
    two distinct entities that happen to share the same
    human-readable name.

    Therefore the later physical plan can use:

        GROUP BY
            customer.id,
            customer.name

    while projecting only the readable customer name.

    This model contains NO concept-specific mappings.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    # Business concept being grouped.
    concept: str

    # Physical ontology table.
    table_key: str

    # ======================================================
    # Stable entity identity
    # ======================================================

    identity_column_key: str

    identity_column_name: str

    # ======================================================
    # Human-readable entity display
    # ======================================================

    display_column_key: str

    display_column_name: str


class ColumnResolution(BaseModel):
    """
    Complete column-resolution result for one query.

    Contains:

    - zero or more resolved filter columns
    - optional resolved measure column
    - zero or more resolved default projection columns
    - zero or more resolved entity groupings

    Grouping resolution is separate from normal attribute
    scoring because entity identity/display roles are
    explicit ontology metadata:

        is_primary_key = True
        semantic_role = "display_name"

    rather than fuzzy business-language guesses.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    filters: list[
        ResolvedFilterColumn
    ] = Field(
        default_factory=list
    )

    measure: (
        ResolvedMeasureColumn
        | None
    ) = None

    projections: list[
        ResolvedProjectionColumn
    ] = Field(
        default_factory=list
    )

    groupings: list[
        ResolvedEntityGrouping
    ] = Field(
        default_factory=list
    )