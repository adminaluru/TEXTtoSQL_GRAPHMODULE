import re
from typing import Any

from planning.models import (
    QueryAggregation,
    QueryExistence,
    QueryFilter,
    QueryGrouping,
    QueryJoin,
    QueryPlan,
    QueryProjection,
)

from sql_compiler.models import (
    CompiledQuery,
)


class DeterministicSQLCompiler:
    """
    Compile a validated QueryPlan into PostgreSQL SQL.

    IMPORTANT:

    This component performs NO business reasoning.

    It does NOT:
    - call an LLM
    - call Neo4j
    - resolve concepts
    - discover tables
    - discover columns
    - choose join paths
    - choose grain

    All of those decisions must already exist
    inside QueryPlan.

    This compiler only converts the physical plan
    into parameterized PostgreSQL SQL.
    """

    ALLOWED_AGGREGATIONS = {
        "SUM",
        "COUNT",
        "AVG",
        "MIN",
        "MAX",
    }

    ALLOWED_OPERATORS = {
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "LIKE",
        "ILIKE",
        "IN",
    }

    ALLOWED_JOIN_TYPES = {
        "INNER",
        "LEFT",
    }

    IDENTIFIER_PATTERN = re.compile(
        r"^[A-Za-z_][A-Za-z0-9_]*$"
    )

    # ======================================================
    # Public API
    # ======================================================

    def compile(
        self,
        plan: QueryPlan,
    ) -> CompiledQuery:
        """
        Compile QueryPlan into SQL + parameters.
        """

        if not plan.tables:
            raise ValueError(
                "Cannot compile QueryPlan "
                "without tables."
            )

        self._validate_tables(
            plan.tables
        )

        aliases = self._build_aliases(
            plan.tables
        )

        select_clause = (
            self._compile_select(
                plan=plan,
                aliases=aliases,
            )
        )

        from_clause = (
            self._compile_from(
                plan=plan,
                aliases=aliases,
            )
        )

        join_clauses = (
            self._compile_joins(
                plan=plan,
                aliases=aliases,
            )
        )

        filter_where_clause, parameters = (
            self._compile_filters(
                filters=plan.filters,
                aliases=aliases,
            )
        )

        (
            existence_clauses,
            existence_parameters,
        ) = (
            self._compile_existence_conditions(
                conditions=
                    plan.existence_conditions,

                outer_aliases=
                    aliases,
            )
        )

        parameters.extend(
            existence_parameters
        )

        predicates = []

        if filter_where_clause:

            predicates.append(
                filter_where_clause[
                    len("WHERE "):
                ]
            )

        predicates.extend(
            existence_clauses
        )

        where_clause = ""

        if predicates:

            where_clause = (
                "WHERE "
                + "\n  AND ".join(
                    predicates
                )
            )

        sql_parts = [
            select_clause,
            from_clause,
        ]

        sql_parts.extend(
            join_clauses
        )

        if where_clause:
            sql_parts.append(
                where_clause
            )

        group_by_clause = (
            self._compile_group_by(
                groupings=
                    plan.groupings,

                aliases=
                    aliases,
            )
        )

        if group_by_clause:
            sql_parts.append(
                group_by_clause
            )

        if plan.limit is not None:
            sql_parts.append(
                f"LIMIT {plan.limit}"
            )

        sql = "\n".join(
            sql_parts
        )

        return CompiledQuery(
            sql=sql,
            parameters=parameters,
        )

    # ======================================================
    # SELECT
    # ======================================================

    def _compile_select(
        self,
        plan: QueryPlan,
        aliases: dict[str, str],
    ) -> str:
        """
        Compile SELECT expressions.

        Supported shapes:

        1. Scalar aggregate

            SELECT SUM(...)

        2. Grouped aggregate

            SELECT
                display_column AS alias,
                SUM(...)

            ...
            GROUP BY
                identity_column,
                display_column

        3. Non-aggregate LIST / lookup projection

            SELECT
                column_a AS alias_a,
                column_b AS alias_b

        The compiler performs no business reasoning.
        Projection fields must already be resolved inside
        QueryPlan.
        """

        if (
            not plan.aggregations
            and not plan.projections
        ):
            raise ValueError(
                "QueryPlan contains no SELECT "
                "expressions."
            )

        if (
            plan.projections
            and not plan.aggregations
        ):

            if (
                plan.intent
                not in {
                    "list",
                    "lookup",
                }
            ):

                raise ValueError(
                    "Projection-only QueryPlan requires "
                    "LIST or lookup intent."
                )

            if plan.groupings:

                raise ValueError(
                    "Projection-only LIST/lookup QueryPlan "
                    "cannot contain GROUP BY columns in the "
                    "current compiler contract."
                )

        if (
            plan.projections
            and plan.aggregations
            and not plan.groupings
        ):
            raise ValueError(
                "Grouped aggregate QueryPlan contains "
                "projections but no GROUP BY columns."
            )

        expressions = []

        for projection in plan.projections:

            expressions.append(
                self._compile_projection(
                    projection=
                        projection,

                    aliases=
                        aliases,
                )
            )

        for aggregation in plan.aggregations:

            expressions.append(
                self._compile_aggregation(
                    aggregation=
                        aggregation,

                    aliases=
                        aliases,
                )
            )

        return (
            "SELECT "
            + ", ".join(
                expressions
            )
        )

    def _compile_projection(
        self,
        projection: QueryProjection,
        aliases: dict[str, str],
    ) -> str:

        if (
            projection.table
            not in aliases
        ):
            raise ValueError(
                "Projection references table "
                "outside QueryPlan: "
                f"{projection.table}"
            )

        self._validate_identifier(
            projection.column
        )

        self._validate_identifier(
            projection.alias
        )

        table_alias = aliases[
            projection.table
        ]

        return (
            f"{table_alias}."
            f"{projection.column} "
            f"AS {projection.alias}"
        )

    def _compile_aggregation(
        self,
        aggregation: QueryAggregation,
        aliases: dict[str, str],
    ) -> str:

        function = (
            aggregation.function.upper()
        )

        if (
            function
            not in self.ALLOWED_AGGREGATIONS
        ):
            raise ValueError(
                "Unsupported aggregation "
                f"function: {function}"
            )

        if aggregation.table not in aliases:
            raise ValueError(
                "Aggregation references table "
                "outside QueryPlan: "
                f"{aggregation.table}"
            )

        self._validate_identifier(
            aggregation.column
        )

        self._validate_identifier(
            aggregation.alias
        )

        table_alias = aliases[
            aggregation.table
        ]

        return (
            f"{function}("
            f"{table_alias}."
            f"{aggregation.column}"
            f") AS "
            f"{aggregation.alias}"
        )

    # ======================================================
    # FROM
    # ======================================================

    def _compile_from(
        self,
        plan: QueryPlan,
        aliases: dict[str, str],
    ) -> str:

        base_table = plan.tables[0]

        return (
            f"FROM {base_table} "
            f"AS {aliases[base_table]}"
        )

    # ======================================================
    # JOIN
    # ======================================================

    def _compile_joins(
        self,
        plan: QueryPlan,
        aliases: dict[str, str],
    ) -> list[str]:
        """
        Build joins in connectivity order.

        QueryJoin direction does not need to match
        traversal direction.

        Example:

        Base table:
            Customer

        Relationship metadata:
            PaymentReceipt.customer_id
                =
            Customer.id

        Compiler produces:

            INNER JOIN PaymentReceipt AS t1
                ON t1.customer_id = t0.id
        """

        if len(plan.tables) == 1:
            if plan.joins:
                raise ValueError(
                    "Single-table QueryPlan "
                    "unexpectedly contains joins."
                )

            return []

        if not plan.joins:
            raise ValueError(
                "Multi-table QueryPlan "
                "contains no joins."
            )

        joined_tables = {
            plan.tables[0]
        }

        remaining = list(
            plan.joins
        )

        clauses = []

        while remaining:

            progress = False

            for join in list(
                remaining
            ):

                self._validate_join(
                    join
                )

                left_known = (
                    join.left_table
                    in joined_tables
                )

                right_known = (
                    join.right_table
                    in joined_tables
                )

                # --------------------------------------
                # Left side already joined.
                # Add right table.
                # --------------------------------------

                if (
                    left_known
                    and not right_known
                ):

                    new_table = (
                        join.right_table
                    )

                # --------------------------------------
                # Right side already joined.
                # Add left table.
                # --------------------------------------

                elif (
                    right_known
                    and not left_known
                ):

                    new_table = (
                        join.left_table
                    )

                # --------------------------------------
                # Both are already joined.
                #
                # This could represent composite join
                # metadata. We fail closed rather than
                # silently generating incorrect SQL.
                # --------------------------------------

                elif (
                    left_known
                    and right_known
                ):

                    raise ValueError(
                        "QueryPlan contains a join "
                        "between two tables that are "
                        "already connected. "
                        "Composite/cyclic join "
                        "compilation requires an "
                        "explicit composite-join model."
                    )

                # --------------------------------------
                # Neither side is connected yet.
                # Try another join first.
                # --------------------------------------

                else:
                    continue

                if (
                    new_table
                    not in aliases
                ):
                    raise ValueError(
                        "Join references table "
                        "outside QueryPlan: "
                        f"{new_table}"
                    )

                left_alias = aliases[
                    join.left_table
                ]

                right_alias = aliases[
                    join.right_table
                ]

                new_alias = aliases[
                    new_table
                ]

                clause = (
                    f"{join.join_type} JOIN "
                    f"{new_table} "
                    f"AS {new_alias}\n"
                    f"    ON "
                    f"{left_alias}."
                    f"{join.left_column} "
                    f"= "
                    f"{right_alias}."
                    f"{join.right_column}"
                )

                clauses.append(
                    clause
                )

                joined_tables.add(
                    new_table
                )

                remaining.remove(
                    join
                )

                progress = True

                break

            if not progress:
                unresolved = [
                    (
                        join.left_table,
                        join.right_table,
                    )
                    for join in remaining
                ]

                raise ValueError(
                    "Unable to connect all QueryPlan "
                    "joins from the base table. "
                    f"Unresolved joins: {unresolved}"
                )

        missing_tables = (
            set(plan.tables)
            - joined_tables
        )

        if missing_tables:
            raise ValueError(
                "QueryPlan contains disconnected "
                f"tables: {sorted(missing_tables)}"
            )

        return clauses

    def _validate_join(
        self,
        join: QueryJoin,
    ) -> None:

        if (
            join.join_type
            not in self.ALLOWED_JOIN_TYPES
        ):
            raise ValueError(
                "Unsupported join type: "
                f"{join.join_type}"
            )

        self._validate_table_identifier(
            join.left_table
        )

        self._validate_table_identifier(
            join.right_table
        )

        self._validate_identifier(
            join.left_column
        )

        self._validate_identifier(
            join.right_column
        )

        # Only validated physical relationships should
        # normally reach SQL compilation.

        if (
            join.validation_status
            and join.validation_status
            not in {
                "confirmed",
                "composite_join_recommended",
            }
        ):
            raise ValueError(
                "Refusing to compile join with "
                "unsafe validation status: "
                f"{join.validation_status}"
            )

    # ======================================================
    # WHERE
    # ======================================================

    def _compile_filters(
        self,
        filters: list[QueryFilter],
        aliases: dict[str, str],
    ) -> tuple[
        str,
        list[Any],
    ]:

        if not filters:
            return "", []

        clauses = []
        parameters: list[Any] = []

        for query_filter in filters:

            if (
                query_filter.table
                not in aliases
            ):
                raise ValueError(
                    "Filter references table "
                    "outside QueryPlan: "
                    f"{query_filter.table}"
                )

            self._validate_identifier(
                query_filter.column
            )

            operator = (
                query_filter.operator
                .upper()
            )

            if (
                operator
                not in self.ALLOWED_OPERATORS
            ):
                raise ValueError(
                    "Unsupported filter operator: "
                    f"{operator}"
                )

            table_alias = aliases[
                query_filter.table
            ]

            # ------------------------------------------
            # IN
            # ------------------------------------------

            if operator == "IN":

                value = query_filter.value

                if not isinstance(
                    value,
                    (
                        list,
                        tuple,
                        set,
                    ),
                ):
                    raise ValueError(
                        "IN filter requires "
                        "a collection value."
                    )

                values = list(
                    value
                )

                if not values:
                    raise ValueError(
                        "IN filter cannot use "
                        "an empty collection."
                    )

                placeholders = ", ".join(
                    "%s"
                    for _ in values
                )

                clauses.append(
                    f"{table_alias}."
                    f"{query_filter.column} "
                    f"IN ({placeholders})"
                )

                parameters.extend(
                    values
                )

            # ------------------------------------------
            # Normal binary operator
            # ------------------------------------------

            else:

                clauses.append(
                    f"{table_alias}."
                    f"{query_filter.column} "
                    f"{operator} %s"
                )

                parameters.append(
                    query_filter.value
                )

        where_clause = (
            "WHERE "
            + "\n  AND ".join(
                clauses
            )
        )

        return (
            where_clause,
            parameters,
        )

    # ======================================================
    # EXISTS / semi-join
    # ======================================================

    def _compile_existence_conditions(
        self,
        conditions: list[
            QueryExistence
        ],
        outer_aliases: dict[
            str,
            str,
        ],
    ) -> tuple[
        list[str],
        list[Any],
    ]:
        """
        Compile graph-planned existence conditions into
        deterministic correlated EXISTS predicates.

        The outer result table is never repeated as a
        result-producing JOIN.

        Example:

            outer:
                Invoice AS t0

            existence path:
                Invoice -> BrokenPromiseToPay

        becomes:

            EXISTS (
                SELECT 1
                FROM BrokenPromiseToPay AS e0_0
                WHERE e0_0.invoice_id = t0.id
            )

        Multi-hop example:

            Customer -> Invoice -> Claim

        becomes:

            EXISTS (
                SELECT 1
                FROM Invoice AS e0_0
                INNER JOIN Claim AS e0_1
                    ON ...
                WHERE ... = t0.id
            )
        """

        clauses: list[str] = []
        parameters: list[Any] = []

        for index, condition in enumerate(
            conditions
        ):

            (
                clause,
                condition_parameters,
            ) = (
                self._compile_existence_condition(
                    condition=
                        condition,

                    outer_aliases=
                        outer_aliases,

                    condition_index=
                        index,
                )
            )

            clauses.append(
                clause
            )

            parameters.extend(
                condition_parameters
            )

        return (
            clauses,
            parameters,
        )

    def _compile_existence_condition(
        self,
        condition: QueryExistence,
        outer_aliases: dict[
            str,
            str,
        ],
        condition_index: int,
    ) -> tuple[
        str,
        list[Any],
    ]:

        anchor_table = (
            condition.anchor_table
        )

        if (
            anchor_table
            not in outer_aliases
        ):

            raise ValueError(
                "Existence anchor table is not part "
                "of the outer QueryPlan: "
                f"{anchor_table}"
            )

        if (
            not condition.tables
            or condition.tables[0]
            != anchor_table
        ):

            raise ValueError(
                "Existence path must begin with its "
                "declared anchor table."
            )

        if (
            len(condition.tables)
            < 2
        ):

            raise ValueError(
                "Existence condition requires at least "
                "one related physical table."
            )

        if (
            len(condition.tables)
            != len(
                set(
                    condition.tables
                )
            )
        ):

            raise ValueError(
                "Existence condition contains duplicate "
                "physical tables."
            )

        for table in condition.tables:

            self._validate_table_identifier(
                table
            )

        if not condition.joins:

            raise ValueError(
                "Existence condition contains no "
                "physical joins."
            )

        # --------------------------------------------------
        # Subquery table aliases
        #
        # Anchor stays in the OUTER query.
        # Every remaining path table receives a local alias.
        # --------------------------------------------------

        inner_tables = (
            condition.tables[1:]
        )

        inner_aliases = {
            table:
                f"e{condition_index}_{table_index}"

            for table_index, table
            in enumerate(
                inner_tables
            )
        }

        all_aliases = {
            anchor_table:
                outer_aliases[
                    anchor_table
                ],

            **inner_aliases,
        }

        # --------------------------------------------------
        # Validate every join and locate the correlation
        # edge between outer anchor and first inner table.
        # --------------------------------------------------

        for join in condition.joins:

            self._validate_join(
                join
            )

            if (
                join.left_table
                not in condition.tables
                or join.right_table
                not in condition.tables
            ):

                raise ValueError(
                    "Existence join references a table "
                    "outside its existence path."
                )

        first_inner_table = (
            inner_tables[0]
        )

        correlation_candidates = [
            join

            for join
            in condition.joins

            if (
                {
                    join.left_table,
                    join.right_table,
                }
                ==
                {
                    anchor_table,
                    first_inner_table,
                }
            )
        ]

        if (
            len(correlation_candidates)
            != 1
        ):

            raise ValueError(
                "Existence condition requires exactly "
                "one correlation join between the "
                "anchor table and first inner table. "
                f"Found: "
                f"{len(correlation_candidates)}"
            )

        correlation_join = (
            correlation_candidates[0]
        )

        correlation_predicate = (
            self._compile_join_predicate(
                join=
                    correlation_join,

                aliases=
                    all_aliases,
            )
        )

        remaining_joins = [
            join

            for join
            in condition.joins

            if join is not correlation_join
        ]

        # --------------------------------------------------
        # FROM starts at the first table after the anchor.
        # --------------------------------------------------

        first_alias = (
            inner_aliases[
                first_inner_table
            ]
        )

        sql_parts = [
            "EXISTS (",
            "    SELECT 1",
            (
                f"    FROM "
                f"{first_inner_table} "
                f"AS {first_alias}"
            ),
        ]

        # --------------------------------------------------
        # Join the rest of the existence path inside the
        # subquery without ever joining the outer anchor.
        # --------------------------------------------------

        joined_tables = {
            first_inner_table
        }

        remaining = list(
            remaining_joins
        )

        while remaining:

            progress = False

            for join in list(
                remaining
            ):

                if (
                    join.left_table
                    == anchor_table
                    or join.right_table
                    == anchor_table
                ):

                    raise ValueError(
                        "Existence path contains an "
                        "unexpected additional anchor join."
                    )

                left_known = (
                    join.left_table
                    in joined_tables
                )

                right_known = (
                    join.right_table
                    in joined_tables
                )

                if (
                    left_known
                    and not right_known
                ):

                    new_table = (
                        join.right_table
                    )

                elif (
                    right_known
                    and not left_known
                ):

                    new_table = (
                        join.left_table
                    )

                elif (
                    left_known
                    and right_known
                ):

                    # Already connected by an earlier path
                    # edge. Skip duplicate/redundant edge.
                    remaining.remove(
                        join
                    )

                    progress = True
                    break

                else:
                    continue

                if (
                    new_table
                    not in inner_aliases
                ):

                    raise ValueError(
                        "Existence join attempts to add "
                        "a table outside the inner path: "
                        f"{new_table}"
                    )

                predicate = (
                    self._compile_join_predicate(
                        join=
                            join,

                        aliases=
                            all_aliases,
                    )
                )

                sql_parts.append(
                    (
                        f"    {join.join_type} JOIN "
                        f"{new_table} "
                        f"AS "
                        f"{inner_aliases[new_table]}"
                    )
                )

                sql_parts.append(
                    f"        ON {predicate}"
                )

                joined_tables.add(
                    new_table
                )

                remaining.remove(
                    join
                )

                progress = True
                break

            if not progress:

                unresolved = [
                    (
                        join.left_table,
                        join.right_table,
                    )

                    for join
                    in remaining
                ]

                raise ValueError(
                    "Unable to connect existence "
                    "subquery path. Unresolved joins: "
                    f"{unresolved}"
                )

        missing_inner_tables = (
            set(inner_tables)
            - joined_tables
        )

        if missing_inner_tables:

            raise ValueError(
                "Existence condition contains "
                "disconnected inner tables: "
                f"{sorted(missing_inner_tables)}"
            )

        # --------------------------------------------------
        # Inner filters + outer correlation become the
        # subquery WHERE clause.
        # --------------------------------------------------

        (
            inner_where,
            parameters,
        ) = (
            self._compile_filters(
                filters=
                    condition.filters,

                aliases=
                    all_aliases,
            )
        )

        predicates = [
            correlation_predicate
        ]

        if inner_where:

            predicates.append(
                inner_where[
                    len("WHERE "):
                ]
            )

        sql_parts.append(
            (
                "    WHERE "
                + "\n      AND ".join(
                    predicates
                )
            )
        )

        sql_parts.append(
            ")"
        )

        return (
            "\n".join(
                sql_parts
            ),
            parameters,
        )

    def _compile_join_predicate(
        self,
        join: QueryJoin,
        aliases: dict[
            str,
            str,
        ],
    ) -> str:

        if (
            join.left_table
            not in aliases
            or join.right_table
            not in aliases
        ):

            raise ValueError(
                "Join predicate references table "
                "without an assigned SQL alias."
            )

        return (
            f"{aliases[join.left_table]}."
            f"{join.left_column} "
            f"= "
            f"{aliases[join.right_table]}."
            f"{join.right_column}"
        )

    # ======================================================
    # GROUP BY
    # ======================================================

    def _compile_group_by(
        self,
        groupings: list[
            QueryGrouping
        ],
        aliases: dict[
            str,
            str,
        ],
    ) -> str:
        """
        Compile resolved physical GROUP BY columns.

        The compiler performs no business reasoning.
        Identity/display decisions must already exist
        in QueryPlan.

        Example physical plan:

            Customer.id
            Customer.name

        becomes:

            GROUP BY t0.id, t0.name
        """

        if not groupings:
            return ""

        expressions = []
        seen = set()

        for grouping in groupings:

            if (
                grouping.table
                not in aliases
            ):
                raise ValueError(
                    "Grouping references table "
                    "outside QueryPlan: "
                    f"{grouping.table}"
                )

            self._validate_identifier(
                grouping.column
            )

            signature = (
                grouping.table,
                grouping.column,
            )

            if signature in seen:
                continue

            seen.add(
                signature
            )

            table_alias = aliases[
                grouping.table
            ]

            expressions.append(
                f"{table_alias}."
                f"{grouping.column}"
            )

        return (
            "GROUP BY "
            + ", ".join(
                expressions
            )
        )

    # ======================================================
    # Aliases
    # ======================================================

    @staticmethod
    def _build_aliases(
        tables: list[str],
    ) -> dict[str, str]:
        """
        Deterministically assign:

        first table  -> t0
        second table -> t1
        ...
        """

        return {
            table: f"t{index}"
            for index, table
            in enumerate(tables)
        }

    # ======================================================
    # Identifier validation
    # ======================================================

    def _validate_tables(
        self,
        tables: list[str],
    ) -> None:

        if len(tables) != len(
            set(tables)
        ):
            raise ValueError(
                "QueryPlan contains duplicate tables."
            )

        for table in tables:
            self._validate_table_identifier(
                table
            )

    def _validate_table_identifier(
        self,
        table: str,
    ) -> None:
        """
        Accept qualified PostgreSQL identifiers such as:

            public.erp_customer_details

        Reject arbitrary SQL fragments.
        """

        parts = table.split(
            "."
        )

        if len(parts) != 2:
            raise ValueError(
                "Expected schema-qualified table "
                f"identifier, received: {table}"
            )

        for part in parts:
            self._validate_identifier(
                part
            )

    def _validate_identifier(
        self,
        identifier: str,
    ) -> None:

        if not self.IDENTIFIER_PATTERN.fullmatch(
            identifier
        ):
            raise ValueError(
                "Unsafe SQL identifier: "
                f"{identifier}"
            )