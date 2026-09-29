from pydantic import BaseModel, ConfigDict, Field

from ontology.models import (
    PhysicalPath,
    SemanticRelationship,
    TableMetadata,
)


# ==========================================================
# Existence branch
# ==========================================================

class GraphExistenceBranch(BaseModel):
    """
    One ontology-resolved branch used only to qualify the
    outer result through existence semantics.

    Examples:

        Invoice -> BrokenPromiseToPay

        Customer -> Invoice -> InvoiceClaim

    This layer contains graph decisions only.
    It contains NO SQL and NO SELECT-column decisions.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    anchor_concept: str
    target_concept: str

    anchor_table_key: str
    target_table_key: str

    semantic_relationships: list[
        SemanticRelationship
    ] = Field(
        default_factory=list
    )

    physical_path: PhysicalPath


class GraphPlan(BaseModel):
    """
    Ontology-resolved representation of the
    business concepts required by a question.

    This layer contains:
    - resolved tables
    - outer-query semantic relationships
    - outer-query physical paths
    - existence branches

    It does NOT contain:
    - selected SQL columns
    - filters
    - aggregations
    - SQL
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    concepts: list[str] = Field(
        default_factory=list
    )

    tables: list[TableMetadata] = Field(
        default_factory=list
    )

    semantic_relationships: list[
        SemanticRelationship
    ] = Field(
        default_factory=list
    )

    physical_paths: list[
        PhysicalPath
    ] = Field(
        default_factory=list
    )

    existence_branches: list[
        GraphExistenceBranch
    ] = Field(
        default_factory=list
    )