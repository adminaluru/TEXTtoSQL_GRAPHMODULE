from database.neo4j import Neo4jClient

from ontology.models import (
    JoinMetadata,
    PhysicalPath,
    SemanticRelationship,
    TableMetadata,
    ColumnMetadata,
)


class OntologyService:
    """
    Business-facing interface to the Neo4j ontology.

    Responsibilities:
    - Resolve business concepts to physical tables
    - Retrieve physical join paths
    - Retrieve semantic business relationships

    Cypher details remain hidden from the rest
    of the application.
    """

    def __init__(
        self,
        neo4j_client: Neo4jClient,
    ) -> None:
        self._neo4j = neo4j_client

    # ======================================================
    # Concept -> Physical Table
    # ======================================================

    def resolve_table(
        self,
        concept: str,
    ) -> TableMetadata | None:
        """
        Resolve a business concept such as:

        Customer
        Invoice
        PaymentReceipt

        into physical PostgreSQL table metadata.
        """

        query = """
        MATCH (t:Table)

        WHERE toLower(t.business_concept)
              = toLower($concept)

        RETURN
            t.key AS key,
            t.table_name AS table_name,
            t.business_concept AS business_concept,
            t.business_domain AS business_domain

        LIMIT 1
        """

        rows = self._neo4j.execute_read(
            query,
            {
                "concept": concept,
            },
        )

        if not rows:
            return None

        return TableMetadata(
            **rows[0]
        )

    # ======================================================
    # Single Physical Path
    #
    # KEEP THIS METHOD.
    #
    # Used by the original Step 2A validation.
    # ======================================================

    def find_physical_path(
        self,
        start_table_key: str,
        end_table_key: str,
        max_hops: int = 8,
    ) -> PhysicalPath | None:
        """
        Find one shortest physical PostgreSQL join path.

        JOINS_TO direction is deliberately ignored because
        relationships are generally stored FK -> PK while
        planning may traverse in either business direction.
        """

        if max_hops < 1 or max_hops > 10:
            raise ValueError(
                "max_hops must be between 1 and 10"
            )

        query = f"""
        MATCH
            (
                start:Table {{
                    key: $start_table_key
                }}
            ),
            (
                finish:Table {{
                    key: $end_table_key
                }}
            )

        MATCH path = shortestPath(
            (start)-[:JOINS_TO*..{max_hops}]-(finish)
        )

        RETURN
            [
                n IN nodes(path)
                | n.key
            ] AS tables,

            [
                r IN relationships(path)
                |
                {{
                    source_table:
                        startNode(r).key,

                    target_table:
                        endNode(r).key,

                    source_column:
                        r.source_column,

                    target_column:
                        r.target_column,

                    join_condition:
                        r.join_condition,

                    validation_status:
                        r.validation_status,

                    cardinality:
                        r.cardinality
                }}
            ] AS joins
        """

        rows = self._neo4j.execute_read(
            query,
            {
                "start_table_key":
                    start_table_key,

                "end_table_key":
                    end_table_key,
            },
        )

        if not rows:
            return None

        row = rows[0]

        joins = [
            JoinMetadata(**join)
            for join in row["joins"]
        ]

        return PhysicalPath(
            tables=row["tables"],
            joins=joins,
        )

    # ======================================================
    # Multiple Candidate Physical Paths
    #
    # Used by PathRanker.
    # ======================================================

    def find_physical_paths(
        self,
        start_table_key: str,
        end_table_key: str,
        max_hops: int = 8,
        max_paths: int = 50,
    ) -> list[PhysicalPath]:
        """
        Retrieve candidate SIMPLE PostgreSQL join paths.

        Important differences from find_physical_path():

        - retrieves multiple candidates
        - excludes paths that repeat the same Table node
        - removes exact duplicate physical paths
        - does not decide which candidate is best

        PathRanker is responsible for selecting the
        preferred path.
        """

        if max_hops < 1 or max_hops > 10:
            raise ValueError(
                "max_hops must be between 1 and 10"
            )

        if max_paths < 1 or max_paths > 100:
            raise ValueError(
                "max_paths must be between 1 and 100"
            )

        query = f"""
        MATCH
            (
                start:Table {{
                    key: $start_table_key
                }}
            ),
            (
                finish:Table {{
                    key: $end_table_key
                }}
            )

        MATCH path =
            (start)-[:JOINS_TO*1..{max_hops}]-(finish)

        /*
         * Only simple paths.
         *
         * Every Table node must appear exactly once
         * inside the candidate path.
         */
        WHERE ALL(
            n IN nodes(path)
            WHERE single(
                m IN nodes(path)
                WHERE m = n
            )
        )

        WITH path

        ORDER BY length(path) ASC

        LIMIT $max_paths

        RETURN
            [
                n IN nodes(path)
                | n.key
            ] AS tables,

            [
                r IN relationships(path)
                |
                {{
                    source_table:
                        startNode(r).key,

                    target_table:
                        endNode(r).key,

                    source_column:
                        r.source_column,

                    target_column:
                        r.target_column,

                    join_condition:
                        r.join_condition,

                    validation_status:
                        r.validation_status,

                    cardinality:
                        r.cardinality
                }}
            ] AS joins
        """

        rows = self._neo4j.execute_read(
            query,
            {
                "start_table_key":
                    start_table_key,

                "end_table_key":
                    end_table_key,

                "max_paths":
                    max_paths,
            },
        )

        paths: list[PhysicalPath] = []

        # Prevent duplicate physical candidates.
        seen_signatures: set[tuple] = set()

        for row in rows:

            tables = row["tables"]

            # ------------------------------------------------
            # Defensive Python-level cycle protection.
            # ------------------------------------------------

            if len(tables) != len(set(tables)):
                continue

            joins = [
                JoinMetadata(**join)
                for join in row["joins"]
            ]

            # ------------------------------------------------
            # Build a physical signature.
            # ------------------------------------------------

            signature = (
                tuple(tables),
                tuple(
                    (
                        join.source_table,
                        join.target_table,
                        join.source_column,
                        join.target_column,
                        join.join_condition,
                        join.validation_status,
                    )
                    for join in joins
                ),
            )

            if signature in seen_signatures:
                continue

            seen_signatures.add(
                signature
            )

            paths.append(
                PhysicalPath(
                    tables=tables,
                    joins=joins,
                )
            )

        return paths

    # ======================================================
    # Table Columns
    # ======================================================

    def get_table_columns(
        self,
        table_key: str,
    ) -> list[ColumnMetadata]:
        """
        Return all physical columns belonging to one
        ontology Table node.

        ColumnResolver uses this metadata rather than
        hard-coding PostgreSQL fields.
        """

        query = """
        MATCH
            (t:Table {key: $table_key})
            -[:HAS_COLUMN]->
            (c:Column)

        RETURN
            c.key AS key,
            c.name AS name,
            c.qualified_name AS qualified_name,

            t.key AS table_key,
            t.table_name AS table_name,

            c.data_type AS data_type,
            c.category AS category,
            c.description AS description,

            c.ordinal AS ordinal,

            coalesce(
                c.is_primary_key,
                false
            ) AS is_primary_key,

            c.semantic_role
                AS semantic_role,

            c.projection_role
                AS projection_role,

            c.projection_order
                AS projection_order

        ORDER BY c.ordinal
        """

        rows = self._neo4j.execute_read(
            query,
            {
                "table_key": table_key,
            },
        )

        return [
            ColumnMetadata(**row)
            for row in rows
        ]

    # ======================================================
    # Semantic Relationship
    # ======================================================

    def find_semantic_relationship(
        self,
        source_concept: str,
        target_concept: str,
    ) -> SemanticRelationship | None:
        """
        Find a directed business-semantic relationship.

        Example:

            Customer
                --MAKES_PAYMENT-->
            PaymentReceipt

        JOINS_TO is deliberately excluded because
        JOINS_TO belongs to the physical SQL layer.
        """

        source_table = self.resolve_table(
            source_concept
        )

        target_table = self.resolve_table(
            target_concept
        )

        if (
            source_table is None
            or target_table is None
        ):
            return None

        query = """
        MATCH
            (source:Table {
                key: $source_key
            })
            -[r]->
            (target:Table {
                key: $target_key
            })

        WHERE type(r) <> 'JOINS_TO'

        RETURN
            source.key
                AS source_table_key,

            source.business_concept
                AS source_concept,

            type(r)
                AS relationship_type,

            target.key
                AS target_table_key,

            target.business_concept
                AS target_concept,

            r.join_condition
                AS join_condition,

            r.cardinality
                AS cardinality,

            r.validation_status
                AS validation_status

        LIMIT 1
        """

        rows = self._neo4j.execute_read(
            query,
            {
                "source_key":
                    source_table.key,

                "target_key":
                    target_table.key,
            },
        )

        if not rows:
            return None

        row = rows[0]

        return SemanticRelationship(
            source_table_key=
                row["source_table_key"],

            source_concept=
                row["source_concept"],

            relationship_type=
                row["relationship_type"],

            target_table_key=
                row["target_table_key"],

            target_concept=
                row["target_concept"],

            join_condition=
                row.get("join_condition"),

            cardinality=
                row.get("cardinality"),

            validation_status=
                row.get("validation_status"),
        )

    # ======================================================
    # Semantic Relationship Between Physical Tables
    #
    # NEW:
    #
    # Used by GraphPlanner when a physical path introduces
    # intermediate bridge tables.
    #
    # Example:
    #
    # Physical path:
    #
    # CustomerTable
    #      ↓
    # InvoiceTable
    #      ↓
    # InvoiceClaimTable
    #
    # GraphPlanner can ask whether each adjacent table pair
    # is also connected by an authorized business-semantic
    # relationship.
    #
    # Physical traversal direction is deliberately ignored.
    #
    # The returned SemanticRelationship preserves the
    # ACTUAL semantic direction stored in Neo4j.
    # ======================================================

    def find_semantic_relationship_between_tables(
        self,
        first_table_key: str,
        second_table_key: str,
    ) -> SemanticRelationship | None:
        """
        Find a business-semantic relationship between two
        physical ontology Table nodes.

        Unlike find_semantic_relationship(), callers already
        know the physical table keys.

        The lookup is intentionally direction-agnostic:

            first -> second

        OR:

            second -> first

        This is necessary because physical JOIN traversal
        direction and business-semantic direction are not
        necessarily the same.

        Example:

            Physical FK direction:

                Invoice.customer_id
                    ->
                Customer.id

            Business semantic direction:

                Customer
                    --RECEIVES_INVOICE-->
                Invoice

        JOINS_TO is explicitly excluded.

        No table-specific or concept-specific assumptions
        exist in this method.
        """

        if not first_table_key:
            raise ValueError(
                "first_table_key cannot be empty."
            )

        if not second_table_key:
            raise ValueError(
                "second_table_key cannot be empty."
            )

        if first_table_key == second_table_key:
            return None

        query = """
        MATCH
            (
                first:Table {
                    key: $first_table_key
                }
            ),
            (
                second:Table {
                    key: $second_table_key
                }
            )

        MATCH
            (first)-[r]-(second)

        WHERE type(r) <> 'JOINS_TO'

        RETURN
            startNode(r).key
                AS source_table_key,

            startNode(r).business_concept
                AS source_concept,

            type(r)
                AS relationship_type,

            endNode(r).key
                AS target_table_key,

            endNode(r).business_concept
                AS target_concept,

            r.join_condition
                AS join_condition,

            r.cardinality
                AS cardinality,

            r.validation_status
                AS validation_status

        LIMIT 1
        """

        rows = self._neo4j.execute_read(
            query,
            {
                "first_table_key":
                    first_table_key,

                "second_table_key":
                    second_table_key,
            },
        )

        if not rows:
            return None

        row = rows[0]

        return SemanticRelationship(
            source_table_key=
                row["source_table_key"],

            source_concept=
                row["source_concept"],

            relationship_type=
                row["relationship_type"],

            target_table_key=
                row["target_table_key"],

            target_concept=
                row["target_concept"],

            join_condition=
                row.get("join_condition"),

            cardinality=
                row.get("cardinality"),

            validation_status=
                row.get("validation_status"),
        )