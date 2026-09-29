import re
from collections import Counter
from pathlib import Path

from database.neo4j import Neo4jClient


COLUMN_FILE = Path(
    r"Y:\Ai POC Projects\FlowOne POCS"
    r"\metadata_source"
    r"\neo4j_column_layer_only.cypher"
)


COLUMN_PATTERN = re.compile(
    r"MERGE\s*\(c:Column\s*\{\s*key:\s*'([^']+)'\s*\}\)",
    re.IGNORECASE,
)


def main() -> None:

    if not COLUMN_FILE.exists():
        raise FileNotFoundError(
            f"Column layer file not found: {COLUMN_FILE}"
        )

    text = COLUMN_FILE.read_text(
        encoding="utf-8"
    )

    # --------------------------------------------------
    # Extract Column keys from Cypher
    # --------------------------------------------------

    column_keys = COLUMN_PATTERN.findall(
        text
    )

    counts = Counter(
        column_keys
    )

    unique_column_keys = sorted(
        counts.keys()
    )

    duplicate_keys = {
        key: count
        for key, count in counts.items()
        if count > 1
    }

    # --------------------------------------------------
    # Derive owning table key
    #
    # public.erp_customer_details.name
    # ->
    # public.erp_customer_details
    # --------------------------------------------------

    column_table_keys = set()

    malformed_column_keys = []

    for key in unique_column_keys:

        if "." not in key:
            malformed_column_keys.append(
                key
            )
            continue

        table_key = key.rsplit(
            ".",
            1,
        )[0]

        column_table_keys.add(
            table_key
        )

    # --------------------------------------------------
    # Read current live 40 Table keys from Neo4j
    # --------------------------------------------------

    neo4j = Neo4jClient()

    try:

        rows = neo4j.execute_read(
            """
            MATCH (t:Table)
            RETURN t.key AS key
            ORDER BY t.key
            """,
            {},
        )

        live_table_keys = {
            row["key"]
            for row in rows
        }

    finally:
        neo4j.close()

    # --------------------------------------------------
    # Compare extracted column ownership against
    # current business ontology
    # --------------------------------------------------

    missing_parent_tables = sorted(
        column_table_keys
        - live_table_keys
    )

    tables_without_columns = sorted(
        live_table_keys
        - column_table_keys
    )

    # --------------------------------------------------
    # Results
    # --------------------------------------------------

    print("=" * 70)
    print("COLUMN LAYER PREFLIGHT")
    print("=" * 70)

    print()
    print(
        "Column MERGE statements:",
        len(column_keys),
    )

    print(
        "Unique Column keys:",
        len(unique_column_keys),
    )

    print(
        "Current Neo4j Table nodes:",
        len(live_table_keys),
    )

    print(
        "Tables represented by extracted columns:",
        len(column_table_keys),
    )

    print()

    print("Duplicate Column keys:")
    if duplicate_keys:
        for key, count in sorted(
            duplicate_keys.items()
        ):
            print(
                f"  {key} -> {count}"
            )
    else:
        print("  NONE")

    print()

    print(
        "Extracted columns whose parent table "
        "is NOT in current Neo4j:"
    )

    if missing_parent_tables:
        for table_key in missing_parent_tables:
            print(
                f"  {table_key}"
            )
    else:
        print("  NONE")

    print()

    print(
        "Current Neo4j tables with NO extracted columns:"
    )

    if tables_without_columns:
        for table_key in tables_without_columns:
            print(
                f"  {table_key}"
            )
    else:
        print("  NONE")

    print()

    print("Malformed Column keys:")
    if malformed_column_keys:
        for key in malformed_column_keys:
            print(
                f"  {key}"
            )
    else:
        print("  NONE")

    print()
    print("=" * 70)

    safe = (
        not duplicate_keys
        and not missing_parent_tables
        and not tables_without_columns
        and not malformed_column_keys
    )

    if safe:
        print(
            "STATUS: SAFE TO LOAD COLUMN LAYER"
        )
    else:
        print(
            "STATUS: REVIEW REQUIRED BEFORE LOAD"
        )

    print("=" * 70)


if __name__ == "__main__":
    main()