from pydantic import BaseModel


class TableMetadata(BaseModel):
    """
    Canonical representation of a physical PostgreSQL
    table resolved from the Neo4j ontology.
    """

    key: str
    table_name: str

    business_concept: str | None = None
    business_domain: str | None = None


class ColumnMetadata(BaseModel):
    """
    Canonical representation of a physical PostgreSQL
    column stored in the Neo4j ontology.

    Optional ontology metadata can describe both:

    - semantic business roles
    - default projection behavior

    Examples:

        semantic_role = "display_name"

        projection_role = "default"
        projection_order = 1

    This keeps runtime planning generic.

    Python does not need mappings such as:

        Invoice -> invoice_num, amount, balance, status
        Customer -> name, customer_num, ...

    Those decisions belong in ontology metadata.
    """

    key: str
    name: str
    qualified_name: str

    table_key: str
    table_name: str

    data_type: str | None = None
    category: str | None = None
    description: str | None = None

    ordinal: int | None = None

    is_primary_key: bool = False

    # ======================================================
    # Business-semantic role
    #
    # Examples:
    #
    #     display_name
    #     business_identifier
    #     status
    # ======================================================

    semantic_role: str | None = None

    # ======================================================
    # Default projection metadata
    #
    # projection_role:
    #
    #     default
    #
    # means the column belongs to the bounded default
    # user-facing projection for its business entity.
    #
    # projection_order controls deterministic output order.
    #
    # Example:
    #
    # Invoice.invoice_num
    #     projection_role = "default"
    #     projection_order = 1
    #
    # Invoice.amount
    #     projection_role = "default"
    #     projection_order = 2
    #
    # Both are optional so existing ontology columns remain
    # backward-compatible.
    # ======================================================

    projection_role: str | None = None

    projection_order: int | None = None


class JoinMetadata(BaseModel):
    """
    One physical database join retrieved from Neo4j.
    """

    source_table: str | None = None
    target_table: str | None = None

    source_column: str | None = None
    target_column: str | None = None

    join_condition: str

    validation_status: str | None = None
    cardinality: str | None = None


class PhysicalPath(BaseModel):
    """
    Physical PostgreSQL path discovered through
    Neo4j JOINS_TO relationships.
    """

    tables: list[str]
    joins: list[JoinMetadata]


class SemanticRelationship(BaseModel):
    source_table_key: str
    source_concept: str

    relationship_type: str

    target_table_key: str
    target_concept: str

    join_condition: str | None = None
    cardinality: str | None = None
    validation_status: str | None = None