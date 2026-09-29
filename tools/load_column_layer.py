import re
from pathlib import Path

from database.neo4j import Neo4jClient


COLUMN_FILE = Path(
    r"Y:\Ai POC Projects\FlowOne POCS"
    r"\metadata_source"
    r"\neo4j_column_layer_only.cypher"
)


COLUMN_KEY_PATTERN = re.compile(
    r"MERGE\s*\(c:Column\s*\{\s*key:\s*'([^']+)'\s*\}\)",
    re.IGNORECASE,
)


TABLE_MERGE_PATTERN = re.compile(
    r"MERGE\s*\(t:Table",
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


def has_executable_content(
    statement: str,
) -> bool:
    """
    Ignore blocks containing comments only.
    """

    executable_lines = []

    for line in statement.splitlines():

        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("//"):
            continue

        executable_lines.append(
            stripped
        )

    return bool(
        executable_lines
    )


def validate_source_file(
    text: str,
) -> None:
    """
    Safety checks before any Neo4j write occurs.
    """

    if ":JOINS_TO" in text:
        raise RuntimeError(
            "Unsafe column-layer file: "
            "JOINS_TO relationship detected."
        )

    if TABLE_MERGE_PATTERN.search(
        text
    ):
        raise RuntimeError(
            "Unsafe column-layer file: "
            "Table MERGE statement detected."
        )

    column_keys = (
        COLUMN_KEY_PATTERN.findall(
            text
        )
    )

    unique_column_keys = set(
        column_keys
    )

    if len(unique_column_keys) != 625:
        raise RuntimeError(
            "Expected exactly 625 unique Column "
            f"definitions, found "
            f"{len(unique_column_keys)}."
        )


def validate_live_graph(
    neo4j: Neo4jClient,
) -> None:
    """
    Verify that we are connected to the expected
    40-table business ontology before writing.
    """

    table_rows = neo4j.execute_read(
        """
        MATCH (t:Table)
        RETURN count(t) AS count
        """,
        {},
    )

    table_count = (
        table_rows[0]["count"]
        if table_rows
        else 0
    )

    if table_count != 40:
        raise RuntimeError(
            "Expected 40 Table nodes before load, "
            f"found {table_count}."
        )

    join_rows = neo4j.execute_read(
        """
        MATCH (:Table)-[r:JOINS_TO]->(:Table)
        RETURN count(r) AS count
        """,
        {},
    )

    join_count = (
        join_rows[0]["count"]
        if join_rows
        else 0
    )

    if join_count != 65:
        raise RuntimeError(
            "Expected 65 JOINS_TO relationships "
            f"before load, found {join_count}."
        )

    semantic_rows = neo4j.execute_read(
        """
        MATCH (:Table)-[r]->(:Table)
        WHERE type(r) <> 'JOINS_TO'
        RETURN count(r) AS count
        """,
        {},
    )

    semantic_count = (
        semantic_rows[0]["count"]
        if semantic_rows
        else 0
    )

    if semantic_count != 65:
        raise RuntimeError(
            "Expected 65 semantic Table-to-Table "
            "relationships before load, "
            f"found {semantic_count}."
        )


def main() -> None:

    if not COLUMN_FILE.exists():
        raise FileNotFoundError(
            f"Column-layer file not found: "
            f"{COLUMN_FILE}"
        )

    text = COLUMN_FILE.read_text(
        encoding="utf-8"
    )

    # ==================================================
    # Safety validation before opening writes
    # ==================================================

    validate_source_file(
        text
    )

    statements = [
        statement
        for statement
        in split_cypher_statements(text)
        if has_executable_content(
            statement
        )
    ]

    print("=" * 70)
    print("FLOWONE COLUMN LAYER LOAD")
    print("=" * 70)

    print(
        f"Source: {COLUMN_FILE}"
    )

    print(
        f"Executable statements: "
        f"{len(statements)}"
    )

    neo4j = Neo4jClient()

    try:

        validate_live_graph(
            neo4j
        )

        print(
            "Pre-load ontology validation: OK"
        )

        print()

        for index, statement in enumerate(
            statements,
            start=1,
        ):

            try:

                neo4j.execute_write(
                    statement
                )

            except Exception:

                print()
                print("=" * 70)
                print(
                    f"LOAD FAILED AT STATEMENT "
                    f"{index}"
                )
                print("=" * 70)

                preview = statement[:1000]

                print(
                    preview
                )

                print("=" * 70)

                raise

            if (
                index % 100 == 0
                or index == len(statements)
            ):

                print(
                    f"Executed "
                    f"{index}/"
                    f"{len(statements)}"
                )

    finally:

        neo4j.close()

    print()
    print("=" * 70)
    print("COLUMN LAYER LOAD COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()