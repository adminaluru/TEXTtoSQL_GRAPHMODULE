import re

from nlu.models import QuestionAnalysis

from ontology.ontology_service import (
    OntologyService,
)

from planning.column_models import (
    ColumnResolution,
)

from planning.graph_models import (
    GraphPlan,
)

from planning.grain_models import (
    GrainDecision,
)

from planning.models import (
    QueryAggregation,
    QueryExistence,
    QueryFilter,
    QueryGrouping,
    QueryJoin,
    QueryPlan,
    QueryProjection,
)

from semantic.models import (
    SemanticQuery,
)


class QueryPlanBuilder:
    """
    Build a fully resolved physical QueryPlan.

    Inputs:
    - canonical SemanticQuery
    - planning-compatible QuestionAnalysis
    - GraphPlan
    - GrainDecision
    - ColumnResolution

    Responsibilities:
    - flatten selected physical paths
    - build physical joins
    - build physical filters
    - physicalize validated aggregation intent
    - physicalize bounded LIST default projections
    - physicalize graph-planned existence branches
    - physicalize validated entity grouping intent
    - build display projections for grouped entities
    - preserve result grain

    Does NOT:
    - call an LLM
    - discover graph paths
    - generate SQL
    - reinterpret user language

    Entity identity is resolved dynamically through
    Neo4j ontology metadata.
    """

    def __init__(
        self,
        ontology_service: OntologyService,
    ) -> None:

        self._ontology = (
            ontology_service
        )

    # ======================================================
    # Public API
    # ======================================================

    def build(
        self,
        semantic_query: SemanticQuery,
        analysis: QuestionAnalysis,
        graph_plan: GraphPlan,
        grain: GrainDecision,
        columns: ColumnResolution,
    ) -> QueryPlan:

        # ==================================================
        # 1. Outer-query physical joins
        #
        # graph_plan.physical_paths contains only
        # result-producing outer paths.
        #
        # Existence branches are physicalized separately and
        # must not leak their tables/joins into the outer
        # query.
        # ==================================================

        joins = self._collect_joins(
            graph_plan
        )

        # ==================================================
        # 2. Physical filters
        # ==================================================

        filters = [
            QueryFilter(
                table=
                    resolved.table_key,

                column=
                    resolved.column_name,

                operator=
                    resolved.operator,

                value=
                    resolved.value,
            )

            for resolved
            in columns.filters
        ]

        # How conditions were applied (e.g. exact date period),
        # reported with the result.
        result_notes = list(
            dict.fromkeys(
                resolved.note
                for resolved in columns.filters
                if resolved.note
            )
        )

        # ==================================================
        # 3. Physical aggregations
        # ==================================================

        aggregations = (
            self._build_aggregations(
                semantic_query=
                    semantic_query,

                graph_plan=
                    graph_plan,

                columns=
                    columns,
            )
        )

        # ==================================================
        # 4. Physical projections + GROUP BY columns
        # ==================================================

        list_projections = (
            self._build_list_projections(
                semantic_query=
                    semantic_query,

                columns=
                    columns,
            )
        )

        (
            grouping_projections,
            groupings,
        ) = (
            self._build_grouping_plan(
                semantic_query=
                    semantic_query,

                columns=
                    columns,
            )
        )

        if (
            list_projections
            and grouping_projections
        ):

            raise ValueError(
                "QueryPlan cannot combine LIST default "
                "projections with grouped-aggregate "
                "display projections in the current "
                "planning contract."
            )

        projections = (
            list_projections
            + grouping_projections
        )

        # ==================================================
        # 5. Grain
        # ==================================================

        plan_grain = list(
            grain.result_table_keys
        )

        # ==================================================
        # 6. Physical existence / semi-join conditions
        # ==================================================

        existence_conditions = (
            self._build_existence_conditions(
                graph_plan=
                    graph_plan,
            )
        )

        # ==================================================
        # 7. Outer-query physical tables
        #
        # Build this AFTER all result-producing components
        # are known.
        #
        # Existence-only tables are deliberately excluded.
        # ==================================================

        tables = (
            self._collect_outer_tables(
                graph_plan=
                    graph_plan,

                grain=
                    grain,

                filters=
                    filters,

                projections=
                    projections,

                aggregations=
                    aggregations,

                groupings=
                    groupings,

                joins=
                    joins,
            )
        )

        # ==================================================
        # 8. Final QueryPlan
        # ==================================================

        return QueryPlan(
            intent=
                analysis.intent,

            grain=
                plan_grain,

            tables=
                tables,

            joins=
                joins,

            filters=
                filters,

            projections=
                projections,

            aggregations=
                aggregations,

            groupings=
                groupings,

            existence_conditions=
                existence_conditions,

            limit=
                self._resolve_limit(
                    analysis
                ),

            result_notes=
                result_notes,
        )

    # ======================================================
    # Aggregations
    # ======================================================

    def _build_aggregations(
        self,
        semantic_query: SemanticQuery,
        graph_plan: GraphPlan,
        columns: ColumnResolution,
    ) -> list[QueryAggregation]:

        if not semantic_query.aggregations:

            return []

        # SemanticQuery v1 currently supports exactly one
        # aggregation.
        if (
            len(
                semantic_query.aggregations
            )
            != 1
        ):

            raise ValueError(
                "SemanticQuery v1 requires exactly "
                "one aggregation."
            )

        semantic_aggregation = (
            semantic_query
            .aggregations[0]
        )

        # ==================================================
        # ATTRIBUTE AGGREGATION
        #
        # SUM payment amount
        # AVG invoice amount
        # MIN ...
        # MAX ...
        #
        # ColumnResolver owns attribute -> column mapping.
        # ==================================================

        if (
            semantic_aggregation
            .target_kind
            == "attribute"
        ):

            if columns.measure is None:

                raise ValueError(
                    "Attribute aggregation requires "
                    "a resolved measure column."
                )

            measure = (
                columns.measure
            )

            alias = (
                self._build_aggregation_alias(
                    function=
                        semantic_aggregation
                        .function,

                    column_name=
                        measure.column_name,
                )
            )

            return [
                QueryAggregation(
                    function=
                        semantic_aggregation
                        .function,

                    table=
                        measure.table_key,

                    column=
                        measure.column_name,

                    alias=
                        alias,
                )
            ]

        # ==================================================
        # ENTITY AGGREGATION
        #
        # SemanticQuery v1 supports:
        #
        # COUNT(entity)
        #
        # Example:
        #
        # COUNT Invoice
        #       ↓
        # Invoice physical table
        #       ↓
        # ontology PK metadata
        #       ↓
        # COUNT(invoice.id)
        #
        # ColumnResolver is intentionally bypassed.
        # ==================================================

        if (
            semantic_aggregation
            .target_kind
            == "entity"
        ):

            if (
                semantic_aggregation.function
                != "COUNT"
            ):

                raise ValueError(
                    "SemanticQuery v1 supports "
                    "entity aggregation only for "
                    "COUNT."
                )

            table = (
                self._resolve_concept_table(
                    concept=
                        semantic_aggregation
                        .concept,

                    graph_plan=
                        graph_plan,
                )
            )

            identity_column = (
                self._resolve_entity_identity(
                    concept=
                        semantic_aggregation
                        .concept,

                    table_key=
                        table.key,
                )
            )

            alias = (
                self._build_aggregation_alias(
                    function="COUNT",

                    column_name=
                        identity_column.name,
                )
            )

            return [
                QueryAggregation(
                    function="COUNT",

                    table=
                        table.key,

                    column=
                        identity_column.name,

                    alias=
                        alias,
                )
            ]

        raise ValueError(
            "Unsupported semantic aggregation "
            "target kind: "
            f"{semantic_aggregation.target_kind}"
        )

    # ======================================================
    # Default LIST projection physicalization
    # ======================================================

    def _build_list_projections(
        self,
        semantic_query: SemanticQuery,
        columns: ColumnResolution,
    ) -> list[QueryProjection]:
        """
        Convert ontology-resolved bounded default projection
        columns into physical QueryProjection models.

        This method contains no table-specific or
        concept-specific mappings.

        Example runtime input:

            columns.projections = [
                ResolvedProjectionColumn(...),
                ...
            ]

        becomes:

            QueryProjection(
                table=<resolved table>,
                column=<resolved column>,
                alias=<safe column alias>,
            )

        The specific fields are decided upstream by ontology
        metadata:

            projection_role = "default"
            projection_order = <n>
        """

        resolved_projections = list(
            columns.projections
        )

        if (
            semantic_query.query_type
            != "list"
        ):

            if resolved_projections:

                raise ValueError(
                    "ColumnResolution contains default "
                    "LIST projections but SemanticQuery "
                    "is not a LIST query."
                )

            return []

        if semantic_query.aggregations:

            raise ValueError(
                "LIST QueryPlan cannot contain "
                "aggregation intent."
            )

        if semantic_query.groupings:

            raise ValueError(
                "LIST QueryPlan cannot contain "
                "GROUP BY intent in the current "
                "planning contract."
            )

        if not resolved_projections:

            raise ValueError(
                "LIST QueryPlan requires at least one "
                "resolved default projection column."
            )

        projections: list[
            QueryProjection
        ] = []

        seen_signatures: set[
            tuple[str, str]
        ] = set()

        seen_aliases: set[str] = set()

        previous_order = 0

        for resolved in (
            resolved_projections
        ):

            if (
                resolved.projection_order
                <= previous_order
            ):

                raise ValueError(
                    "Resolved default projections must "
                    "arrive in strictly increasing "
                    "projection_order."
                )

            previous_order = (
                resolved.projection_order
            )

            signature = (
                resolved.table_key,
                resolved.column_name,
            )

            if signature in seen_signatures:

                raise ValueError(
                    "Duplicate default projection "
                    "column in ColumnResolution: "
                    f"{resolved.table_key}."
                    f"{resolved.column_name}"
                )

            seen_signatures.add(
                signature
            )

            alias = (
                self._to_safe_identifier(
                    resolved.column_name
                )
            )

            if not alias:

                raise ValueError(
                    "Unable to build LIST projection "
                    "alias from resolved column: "
                    f"{resolved.column_name}"
                )

            if alias in seen_aliases:

                raise ValueError(
                    "Duplicate LIST projection alias "
                    f"generated: {alias}"
                )

            seen_aliases.add(
                alias
            )

            projections.append(
                QueryProjection(
                    table=
                        resolved.table_key,

                    column=
                        resolved.column_name,

                    alias=
                        alias,
                )
            )

        return projections

    # ======================================================
    # Entity grouping physicalization
    # ======================================================

    def _build_grouping_plan(
        self,
        semantic_query: SemanticQuery,
        columns: ColumnResolution,
    ) -> tuple[
        list[QueryProjection],
        list[QueryGrouping],
    ]:
        """
        Convert resolved entity-grouping metadata into:

        - human-readable SELECT projections
        - stable physical GROUP BY columns

        Example semantic grouping:

            Customer entity

        Resolved metadata:

            identity -> id
            display  -> name

        Physical plan:

            projection:
                name AS customer_name

            groupings:
                id
                name

        No concept-specific/table-specific mapping exists
        here. The identity/display columns were already
        discovered from ontology metadata by ColumnResolver.
        """

        semantic_groupings = list(
            semantic_query.groupings
        )

        resolved_groupings = list(
            columns.groupings
        )

        if not semantic_groupings:

            if resolved_groupings:
                raise ValueError(
                    "ColumnResolution contains groupings "
                    "but SemanticQuery contains no "
                    "grouping intent."
                )

            return (
                [],
                [],
            )

        if (
            len(semantic_groupings)
            != len(resolved_groupings)
        ):

            raise ValueError(
                "Semantic grouping intent and resolved "
                "physical grouping count disagree."
            )

        resolved_by_concept = {}

        for resolved in resolved_groupings:

            if (
                resolved.concept
                in resolved_by_concept
            ):

                raise ValueError(
                    "Multiple resolved entity groupings "
                    "exist for concept "
                    f"'{resolved.concept}'."
                )

            resolved_by_concept[
                resolved.concept
            ] = resolved

        projections: list[
            QueryProjection
        ] = []

        groupings: list[
            QueryGrouping
        ] = []

        seen_groupings: set[
            tuple[str, str]
        ] = set()

        seen_projection_aliases: set[
            str
        ] = set()

        for semantic_grouping in (
            semantic_groupings
        ):

            if (
                semantic_grouping.target_kind
                != "entity"
            ):

                raise ValueError(
                    "QueryPlanBuilder currently supports "
                    "physical grouping only for entity "
                    "GroupingIntent."
                )

            resolved = (
                resolved_by_concept.get(
                    semantic_grouping.concept
                )
            )

            if resolved is None:

                raise ValueError(
                    "No resolved physical entity grouping "
                    "exists for semantic grouping concept "
                    f"'{semantic_grouping.concept}'."
                )

            # ----------------------------------------------
            # Identity GROUP BY
            #
            # Prevents two distinct entities with the same
            # display value from collapsing into one group.
            # ----------------------------------------------

            identity_signature = (
                resolved.table_key,
                resolved.identity_column_name,
            )

            if (
                identity_signature
                not in seen_groupings
            ):

                seen_groupings.add(
                    identity_signature
                )

                groupings.append(
                    QueryGrouping(
                        table=
                            resolved.table_key,

                        column=
                            resolved.identity_column_name,
                    )
                )

            # ----------------------------------------------
            # Display GROUP BY
            #
            # PostgreSQL requires projected non-aggregate
            # columns to participate in GROUP BY.
            # ----------------------------------------------

            display_signature = (
                resolved.table_key,
                resolved.display_column_name,
            )

            if (
                display_signature
                not in seen_groupings
            ):

                seen_groupings.add(
                    display_signature
                )

                groupings.append(
                    QueryGrouping(
                        table=
                            resolved.table_key,

                        column=
                            resolved.display_column_name,
                    )
                )

            # ----------------------------------------------
            # Human-readable projection
            #
            # Alias is generated generically from:
            #
            #     <business concept> + <display column>
            #
            # Customer + name -> customer_name
            # ----------------------------------------------

            alias = (
                self._build_projection_alias(
                    concept=
                        resolved.concept,

                    column_name=
                        resolved.display_column_name,
                )
            )

            if (
                alias
                in seen_projection_aliases
            ):

                raise ValueError(
                    "Duplicate projection alias generated "
                    f"for grouped entity: {alias}"
                )

            seen_projection_aliases.add(
                alias
            )

            projections.append(
                QueryProjection(
                    table=
                        resolved.table_key,

                    column=
                        resolved.display_column_name,

                    alias=
                        alias,
                )
            )

        return (
            projections,
            groupings,
        )

    # ======================================================
    # Projection alias
    # ======================================================

    @classmethod
    def _build_projection_alias(
        cls,
        concept: str,
        column_name: str,
    ) -> str:

        concept_part = (
            cls._to_safe_identifier(
                concept
            )
        )

        column_part = (
            cls._to_safe_identifier(
                column_name
            )
        )

        if not concept_part:
            raise ValueError(
                "Unable to build projection alias from "
                f"business concept: {concept}"
            )

        if not column_part:
            raise ValueError(
                "Unable to build projection alias from "
                f"column name: {column_name}"
            )

        return (
            f"{concept_part}_"
            f"{column_part}"
        )

    @staticmethod
    def _to_safe_identifier(
        value: str,
    ) -> str:
        """
        Convert business/physical names into a deterministic
        SQL-safe alias fragment.

        Examples:

            Customer       -> customer
            PaymentReceipt -> payment_receipt
            legal-name     -> legal_name
        """

        value = str(
            value
            or ""
        ).strip()

        value = re.sub(
            r"([A-Z]+)([A-Z][a-z])",
            r"\1_\2",
            value,
        )

        value = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1_\2",
            value,
        )

        value = re.sub(
            r"[^A-Za-z0-9_]+",
            "_",
            value,
        )

        value = re.sub(
            r"_+",
            "_",
            value,
        )

        value = (
            value
            .strip("_")
            .lower()
        )

        if (
            value
            and value[0].isdigit()
        ):

            value = (
                "_"
                + value
            )

        return value

    # ======================================================
    # Concept -> physical table
    # ======================================================

    @staticmethod
    def _resolve_concept_table(
        concept: str,
        graph_plan: GraphPlan,
    ):

        matches = [
            table
            for table
            in graph_plan.tables

            if (
                table.business_concept
                == concept
            )
        ]

        if len(matches) == 1:

            return matches[0]

        if not matches:

            raise ValueError(
                "No physical table was resolved "
                f"for business concept '{concept}'."
            )

        raise ValueError(
            "Multiple physical tables were resolved "
            f"for business concept '{concept}'."
        )

    # ======================================================
    # Entity identity
    # ======================================================

    def _resolve_entity_identity(
        self,
        concept: str,
        table_key: str,
    ):
        """
        SemanticQuery v1 uses a unique Neo4j primary-key
        column as the physical entity identity.

        Production evolution can later distinguish:

        - database PK
        - business/entity key
        - composite identity

        We fail closed today if identity metadata is not
        unambiguous.
        """

        columns = (
            self._ontology
            .get_table_columns(
                table_key
            )
        )

        if not columns:

            raise ValueError(
                "No column metadata exists for "
                f"entity concept '{concept}' "
                f"on table '{table_key}'."
            )

        primary_keys = [
            column
            for column
            in columns

            if getattr(
                column,
                "is_primary_key",
                False,
            )
        ]

        if len(primary_keys) == 1:

            return primary_keys[0]

        if not primary_keys:

            raise ValueError(
                "Unable to resolve entity identity "
                f"for concept '{concept}'. "
                "No primary-key metadata exists."
            )

        candidates = [
            column.key
            for column
            in primary_keys
        ]

        raise ValueError(
            "Unable to resolve entity identity "
            f"for concept '{concept}'. "
            "Composite/multiple primary-key "
            "columns are not supported in "
            "SemanticQuery v1. "
            f"Candidates: {candidates}"
        )

    # ======================================================
    # Existence / semi-join physicalization
    # ======================================================

    def _build_existence_conditions(
        self,
        graph_plan: GraphPlan,
    ) -> list[QueryExistence]:
        """
        Convert graph-planned existence branches into
        physical QueryExistence models.

        GraphPlanner has already decided:

        - anchor concept/table
        - target concept/table
        - complete physical path
        - semantic authorization

        QueryPlanBuilder performs no graph discovery here.
        """

        result: list[
            QueryExistence
        ] = []

        for branch in (
            graph_plan.existence_branches
        ):

            path = (
                branch.physical_path
            )

            if not path.tables:

                raise ValueError(
                    "Existence branch contains no "
                    "physical tables."
                )

            if (
                path.tables[0]
                != branch.anchor_table_key
            ):

                raise ValueError(
                    "Existence physical path does not "
                    "start at its declared anchor table. "
                    f"Anchor: {branch.anchor_table_key}. "
                    f"Path: {path.tables}"
                )

            if (
                path.tables[-1]
                != branch.target_table_key
            ):

                raise ValueError(
                    "Existence physical path does not "
                    "end at its declared target table. "
                    f"Target: {branch.target_table_key}. "
                    f"Path: {path.tables}"
                )

            joins = self._physical_joins_to_query_joins(
                path.joins
            )

            result.append(
                QueryExistence(
                    anchor_table=
                        branch.anchor_table_key,

                    tables=
                        list(
                            path.tables
                        ),

                    joins=
                        joins,

                    filters=
                        [],
                )
            )

        return result

    # ======================================================
    # Physical join conversion
    # ======================================================

    @staticmethod
    def _physical_joins_to_query_joins(
        physical_joins,
    ) -> list[QueryJoin]:

        joins: list[
            QueryJoin
        ] = []

        seen = set()

        for join in physical_joins:

            if (
                join.source_table is None
                or join.target_table is None
                or join.source_column is None
                or join.target_column is None
            ):

                raise ValueError(
                    "Physical join metadata is incomplete "
                    "and cannot be physicalized."
                )

            signature = (
                join.source_table,
                join.source_column,
                join.target_table,
                join.target_column,
                join.join_condition,
            )

            if signature in seen:
                continue

            seen.add(
                signature
            )

            joins.append(
                QueryJoin(
                    left_table=
                        join.source_table,

                    left_column=
                        join.source_column,

                    right_table=
                        join.target_table,

                    right_column=
                        join.target_column,

                    join_type=
                        "INNER",

                    validation_status=
                        join.validation_status,
                )
            )

        return joins

    # ======================================================
    # Outer-query Tables
    # ======================================================

    @staticmethod
    def _collect_outer_tables(
        graph_plan: GraphPlan,
        grain: GrainDecision,
        filters: list[QueryFilter],
        projections: list[QueryProjection],
        aggregations: list[QueryAggregation],
        groupings: list[QueryGrouping],
        joins: list[QueryJoin],
    ) -> list[str]:
        """
        Collect ONLY tables that participate in the outer
        result-producing query.

        Tables used exclusively inside QueryExistence are
        intentionally excluded.
        """

        values: list[str] = []

        # Result grain must always be available, including
        # a single-table LIST with zero outer joins.
        values.extend(
            grain.result_table_keys
        )

        # Existing outer graph paths remain authoritative.
        for path in graph_plan.physical_paths:
            values.extend(
                path.tables
            )

        # Result-producing expressions/predicates.
        values.extend(
            item.table
            for item in filters
        )

        values.extend(
            item.table
            for item in projections
        )

        values.extend(
            item.table
            for item in aggregations
        )

        values.extend(
            item.table
            for item in groupings
        )

        # Every outer join endpoint must be present.
        for join in joins:

            values.append(
                join.left_table
            )

            values.append(
                join.right_table
            )

        return (
            QueryPlanBuilder
            ._unique_preserve_order(
                values
            )
        )

    # ======================================================
    # Tables
    # ======================================================
    # Joins
    # ======================================================

    @classmethod
    def _collect_joins(
        cls,
        graph_plan: GraphPlan,
    ) -> list[QueryJoin]:

        joins: list[
            QueryJoin
        ] = []

        seen = set()

        for path in graph_plan.physical_paths:

            for query_join in (
                cls
                ._physical_joins_to_query_joins(
                    path.joins
                )
            ):

                signature = (
                    query_join.left_table,
                    query_join.left_column,
                    query_join.right_table,
                    query_join.right_column,
                )

                if signature in seen:
                    continue

                seen.add(
                    signature
                )

                joins.append(
                    query_join
                )

        return joins

    # ======================================================
    # Aggregate Alias
    # ======================================================

    @staticmethod
    def _build_aggregation_alias(
        function: str,
        column_name: str,
    ) -> str:

        return (
            f"{function.lower()}_"
            f"{column_name.lower()}"
        )

    # ======================================================
    # Limit
    # ======================================================

    @staticmethod
    def _resolve_limit(
        analysis: QuestionAnalysis,
    ) -> int | None:

        if analysis.intent in {
            "list",
            "lookup",
        }:

            return 100

        return None

    # ======================================================
    # Utility
    # ======================================================

    @staticmethod
    def _unique_preserve_order(
        values: list[str],
    ) -> list[str]:

        seen = set()

        result = []

        for value in values:

            if value not in seen:

                seen.add(
                    value
                )

                result.append(
                    value
                )

        return result