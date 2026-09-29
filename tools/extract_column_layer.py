import re
from pathlib import Path

from database.neo4j import Neo4jClient


SOURCE_FILE = Path(
    r"Y:\Ai POC Projects\FlowOne POCS"
    r"\metadata_source"
    r"\neo4j_schema_knowledge_graph_40_tables.cypher"
)

OUTPUT_FILE = Path(
    r"Y:\Ai POC Projects\FlowOne POCS"
    r"\metadata_source"
    r"\neo4j_column_layer_only.cypher"
)


COLUMN_KEY_PATTERN = re.compile(
    r"Column\s*\{\s*key:\s*'([^']+)'",
    re.IGNORECASE,
)

TABLE_KEY_PATTERN = re.compile(
    r"Table\s*\{\s*key:\s*'([^']+)'",
    re.IGNORECASE,
)


def split_cypher_statements(
    text: str,
) -> list[str]:
    """
    Split Cypher statements on semicolons while
    respecting quoted strings.
    """

    statements = []
    buffer = []

    in_single_quote = False
    escaped = False

    for char in text:

        buffer.append(char)

        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == "'":
            in_single_quote = (
                not in_single_quote
            )
            continue

        if (
            char == ";"
            and not in_single_quote
        ):
            statement = "".join(
                buffer
            ).strip()

            if statement:
                statements.append(
                    statement
                )

            buffer = []

    remaining = "".join(
        buffer
    ).strip()

    if remaining:
        statements.append(
            remaining
        )

    return statements


def get_live_table_keys() -> set[str]:
    """
    Read the canonical Table keys from the CURRENT
    validated Neo4j ontology.

    This prevents old placeholder/external tables
    from being imported into the active graph.
    """

    neo4j = Neo4jClient()

    try:

        rows = neo4j.execute_read(
            """
            MATCH (t:Table)
            RETURN t.key AS key
            """,
            {},
        )

        return {
            row["key"]
            for row in rows
        }

    finally:
        neo4j.close()


def get_column_parent_table(
    column_key: str,
) -> str | None:
    """
    Example:

    public.erp_customer_details.name
        ->
    public.erp_customer_details
    """

    if "." not in column_key:
        return None

    return column_key.rsplit(
        ".",
        1,
    )[0]


def is_column_layer_statement(
    statement: str,
) -> bool:
    """
    Identify statements belonging to the
    column-metadata layer.
    """

    normalized = " ".join(
        statement.split()
    )

    # Column-specific constraints/indexes

    if (
        "column_key_unique" in normalized
        and "CONSTRAINT" in normalized
    ):
        return True

    if (
        "column_name_range" in normalized
        and "INDEX" in normalized
    ):
        return True

    if (
        "column_category_range" in normalized
        and "INDEX" in normalized
    ):
        return True

    # Column nodes

    if "MERGE (c:Column" in normalized:
        return True

    # Table -> Column ownership

    if ":HAS_COLUMN" in normalized:
        return True

    # Primary key column metadata

    if ":PRIMARY_KEY_COLUMN" in normalized:
        return True

    # Recommended index metadata

    if ":INDEX_RECOMMENDED_ON" in normalized:
        return True

    return False


def belongs_to_live_ontology(
    statement: str,
    live_table_keys: set[str],
) -> bool:
    """
    Retain a statement only if every referenced
    Table/Column belongs to the CURRENT 40-table
    ontology.

    Generic constraint/index statements that do
    not reference a specific table are retained.
    """

    column_keys = (
        COLUMN_KEY_PATTERN.findall(
            statement
        )
    )

    table_keys = (
        TABLE_KEY_PATTERN.findall(
            statement
        )
    )

    referenced_tables = set(
        table_keys
    )

    for column_key in column_keys:

        parent_table = (
            get_column_parent_table(
                column_key
            )
        )

        if parent_table:
            referenced_tables.add(
                parent_table
            )

    # Generic Column constraints / indexes may
    # reference no concrete table.
    if not referenced_tables:
        return True

    return referenced_tables.issubset(
        live_table_keys
    )


def main() -> None:

    if not SOURCE_FILE.exists():
        raise FileNotFoundError(
            f"Source file not found: "
            f"{SOURCE_FILE}"
        )

    live_table_keys = (
        get_live_table_keys()
    )

    if len(live_table_keys) != 40:
        raise RuntimeError(
            "Expected 40 live Table nodes, "
            f"found {len(live_table_keys)}."
        )

    source_text = SOURCE_FILE.read_text(
        encoding="utf-8"
    )

    statements = (
        split_cypher_statements(
            source_text
        )
    )

    column_layer_candidates = [
        statement
        for statement in statements
        if is_column_layer_statement(
            statement
        )
    ]

    selected = [
        statement
        for statement
        in column_layer_candidates
        if belongs_to_live_ontology(
            statement,
            live_table_keys,
        )
    ]

    excluded = [
        statement
        for statement
        in column_layer_candidates
        if not belongs_to_live_ontology(
            statement,
            live_table_keys,
        )
    ]

    header = """
// ==========================================================
// FLOWONE COLUMN METADATA LAYER ONLY
//
// Dynamically filtered against the CURRENT
// Neo4j 40-table ontology.
//
// INCLUDED:
// - Column nodes
// - HAS_COLUMN
// - PRIMARY_KEY_COLUMN
// - INDEX_RECOMMENDED_ON
// - Column constraints/indexes
//
// EXCLUDED:
// - old external placeholder tables
// - old JOINS_TO
// - semantic relationships
// - Table creation/update
// - RelationshipDefinition nodes
//
// Existing 40-table business ontology remains authoritative.
// ==========================================================
""".strip()

    output = (
        header
        + "\n\n"
        + "\n\n".join(
            selected
        )
        + "\n"
    )

    OUTPUT_FILE.write_text(
        output,
        encoding="utf-8",
    )

    print("=" * 70)
    print("COLUMN LAYER EXTRACTION")
    print("=" * 70)

    print(
        "Live Neo4j tables:",
        len(live_table_keys),
    )

    print(
        "Source statements:",
        len(statements),
    )

    print(
        "Column-layer candidates:",
        len(
            column_layer_candidates
        ),
    )

    print(
        "Selected statements:",
        len(selected),
    )

    print(
        "Excluded statements:",
        len(excluded),
    )

    print(
        "Output:",
        OUTPUT_FILE,
    )

    print("=" * 70)


if __name__ == "__main__":
    main()