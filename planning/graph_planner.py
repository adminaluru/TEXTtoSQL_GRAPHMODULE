import logging

from nlu.models import (
    QuestionAnalysis,
)

from observability.perf import (
    PerfTrace,
)

from ontology.models import (
    OntologyAmbiguityError,
    PhysicalPath,
    SemanticRelationship,
    TableMetadata,
)

from ontology.ontology_service import (
    OntologyService,
)

from planning.errors import (
    UnsupportedPredicateError,
)

from planning.graph_models import (
    GraphExistenceBranch,
    GraphPlan,
)

from planning.path_ranker import (
    PathRanker,
)

from semantic.models import (
    SemanticQuery,
)


# ==========================================================
# Performance logger
# ==========================================================

logger = logging.getLogger(
    "uvicorn.error"
)

ONTOLOGY_AMBIGUOUS = (
    "This question uses a business term that is defined more than once, "
    "so it cannot be answered safely yet."
)


class GraphPlanner:
    """
    Resolve business concepts from QuestionAnalysis
    against the Neo4j ontology.

    Responsibilities:

    1. Resolve Concept -> Table
    2. Resolve direct semantic relationships
    3. Retrieve candidate physical paths
    4. Rank physical paths
    5. When a direct semantic edge does not exist,
       validate a multi-hop physical path against the
       semantic ontology
    6. Select the best semantically valid physical path
    7. Build independent existence branches when requested
    8. Build GraphPlan

    Does NOT:

    - generate SQL
    - select columns
    - resolve grain
    - choose aggregations
    - hard-code bridge concepts
    - infer business relationships from FK connectivity

    Important safety rule:

    Physical connectivity alone is NOT sufficient to
    authorize a multi-hop business path.

    Every adjacent physical table pair in an inferred
    bridge path must also have a semantic relationship
    in Neo4j.

    Performance instrumentation measures:

    - concept normalization
    - Concept -> Table resolution
    - direct semantic relationship lookup
    - physical path lookup
    - path ranking
    - semantic bridge validation
    - GraphPlan construction
    - total GraphPlanner latency
    """

    PERF_STAGES = [
        "concept_normalization",
        "table_resolution",
        "semantic_relationship",
        "physical_path_lookup",
        "path_ranking",
        "semantic_path_validation",
        "existence_path_lookup",
        "graph_plan_build",
    ]

    def __init__(
        self,
        ontology_service: OntologyService,
        path_ranker: PathRanker | None = None,
    ) -> None:

        self._ontology = (
            ontology_service
        )

        self._path_ranker = (
            path_ranker
            or PathRanker()
        )

    # ======================================================
    # Public API
    # ======================================================

    def plan(
        self,
        analysis: QuestionAnalysis,
        semantic_query: SemanticQuery | None = None,
    ) -> GraphPlan:

        # An ambiguous ontology is refused, never repaired: a retry could
        # pick a different table and answer a different question.
        try:
            return self._plan(analysis, semantic_query)
        except OntologyAmbiguityError as exc:
            logger.warning("FLOWONE_ONTOLOGY_AMBIGUOUS %s", exc)
            raise UnsupportedPredicateError(ONTOLOGY_AMBIGUOUS) from exc

    def _plan(
        self,
        analysis: QuestionAnalysis,
        semantic_query: SemanticQuery | None = None,
    ) -> GraphPlan:

        with PerfTrace(
            event="FLOWONE_GRAPH_PERF",
            id_field="planner_id",
            stage_order=self.PERF_STAGES,
            logger=logger,
            fields={
                "concepts": 0,
                "table_lookups": 0,
                "semantic_lookups": 0,
                "semantic_bridge_lookups": 0,
                "semantic_path_candidates_checked": 0,
                "physical_path_lookups": 0,
                "candidate_paths": 0,
                "ranking_calls": 0,
                "existence_branches": 0,
            },
        ) as trace:

            # Counters are logged with the trace line.
            counters = trace.fields

            # ==================================================
            # 1. Normalize / deduplicate requested concepts
            # ==================================================

            with trace.stage("concept_normalization"):

                concepts = (
                    self._unique_preserve_order(
                        analysis.concepts
                    )
                )

            counters[
                "concepts"
            ] = len(
                concepts
            )

            # ==================================================
            # No concepts
            # ==================================================

            if not concepts:

                with trace.stage("graph_plan_build"):

                    result = GraphPlan(
                        concepts=[],
                        tables=[],
                        semantic_relationships=[],
                        physical_paths=[],
                        existence_branches=[],
                    )

                return result

            # ==================================================
            # 2. Requested Concept -> Table resolution
            # ==================================================

            resolved_tables: list[
                TableMetadata
            ] = []

            with trace.stage("table_resolution"):

                for concept in concepts:

                    counters[
                        "table_lookups"
                    ] += 1

                    table = (
                        self._ontology
                        .resolve_table(
                            concept
                        )
                    )

                    if table is None:

                        raise ValueError(
                            "Unable to resolve ontology "
                            f"concept: {concept}"
                        )

                    resolved_tables.append(
                        table
                    )

            concept_to_table = {
                table.business_concept:
                    table

                for table
                in resolved_tables

                if table.business_concept
            }

            # ==================================================
            # 2B. Partition outer-query concepts from concepts
            #     used only as existence qualifiers.
            #
            # SemanticQuery owns business meaning.
            # GraphPlanner owns graph topology.
            # ==================================================

            existence_conditions = (
                list(
                    semantic_query
                    .existence_conditions
                )
                if semantic_query is not None
                else []
            )

            outer_concepts = list(
                concepts
            )

            if existence_conditions:

                if (
                    semantic_query is None
                    or not semantic_query.result_concept
                ):

                    raise ValueError(
                        "Existence graph planning requires "
                        "SemanticQuery.result_concept."
                    )

                existence_concepts = {
                    condition.concept

                    for condition
                    in existence_conditions
                }

                protected_outer_concepts = {
                    semantic_query.result_concept,
                }

                protected_outer_concepts.update(
                    item.concept
                    for item
                    in semantic_query.filters
                )

                protected_outer_concepts.update(
                    item.concept
                    for item
                    in semantic_query.groupings
                )

                protected_outer_concepts.update(
                    item.concept
                    for item
                    in semantic_query.aggregations
                )

                outer_concepts = [
                    concept

                    for concept
                    in concepts

                    if (
                        concept
                        not in existence_concepts
                        or concept
                        in protected_outer_concepts
                    )
                ]

                if (
                    semantic_query.result_concept
                    not in outer_concepts
                ):

                    outer_concepts.insert(
                        0,
                        semantic_query.result_concept,
                    )

            semantic_relationships: list[
                SemanticRelationship
            ] = []

            physical_paths: list[
                PhysicalPath
            ] = []

            existence_branches: list[
                GraphExistenceBranch
            ] = []

            # ==================================================
            # Single concept
            # ==================================================

            if (
                len(
                    outer_concepts
                ) == 1
                and not existence_conditions
            ):

                with trace.stage("graph_plan_build"):

                    result = GraphPlan(
                        concepts=
                            concepts,

                        tables=
                            resolved_tables,

                        semantic_relationships=
                            [],

                        physical_paths=
                            [],

                        existence_branches=
                            [],
                    )

                return result

            # ==================================================
            # 3. Resolve adjacent requested concept pairs
            # ==================================================

            for index in range(
                len(outer_concepts) - 1
            ):

                source_concept = (
                    outer_concepts[
                        index
                    ]
                )

                target_concept = (
                    outer_concepts[
                        index + 1
                    ]
                )

                source_table = (
                    concept_to_table[
                        source_concept
                    ]
                )

                target_table = (
                    concept_to_table[
                        target_concept
                    ]
                )

                # ==============================================
                # 3A. Try DIRECT semantic relationship first
                #
                # Existing behavior is preserved.
                # ==============================================

                with trace.stage("semantic_relationship"):

                    counters[
                        "semantic_lookups"
                    ] += 1

                    direct_semantic = (
                        self._ontology
                        .find_semantic_relationship(
                            source_concept=
                                source_concept,

                            target_concept=
                                target_concept,
                        )
                    )

                    # ------------------------------------------
                    # Semantic relationships are directional.
                    #
                    # Try reverse semantic direction when
                    # required.
                    # ------------------------------------------

                    if direct_semantic is None:

                        counters[
                            "semantic_lookups"
                        ] += 1

                        direct_semantic = (
                            self._ontology
                            .find_semantic_relationship(
                                source_concept=
                                    target_concept,

                                target_concept=
                                    source_concept,
                            )
                        )

                # ==============================================
                # 3B. Find physical candidate paths
                #
                # We now do this even when the direct semantic
                # edge does not exist, because the physical
                # graph may contain a legitimate bridge path.
                # ==============================================

                with trace.stage("physical_path_lookup"):

                    counters[
                        "physical_path_lookups"
                    ] += 1

                    candidate_paths = (
                        self._ontology
                        .find_physical_paths(
                            start_table_key=
                                source_table.key,

                            end_table_key=
                                target_table.key,
                        )
                    )

                counters[
                    "candidate_paths"
                ] += len(
                    candidate_paths
                )

                if not candidate_paths:

                    raise ValueError(
                        "No candidate physical paths found "
                        f"between {source_table.key} and "
                        f"{target_table.key}"
                    )

                # ==============================================
                # 3C. DIRECT semantic relationship exists
                #
                # Preserve existing GraphPlanner behavior.
                # ==============================================

                if direct_semantic is not None:

                    trace.mark("path_ranking")

                    physical_path = (
                        self._select_best_path(
                            candidate_paths=
                                candidate_paths,

                            trace=
                                trace,

                            counters=
                                counters,
                        )
                    )

                    if physical_path is None:

                        raise ValueError(
                            "No valid physical path found "
                            f"between {source_table.key} "
                            f"and {target_table.key}"
                        )

                    semantic_relationships.append(
                        direct_semantic
                    )

                    physical_paths.append(
                        physical_path
                    )

                    continue

                # ==============================================
                # 3D. No direct semantic relationship
                #
                # NEW generic multi-hop fallback.
                #
                # Candidate physical paths are considered in
                # PathRanker preference order.
                #
                # Each candidate must pass semantic validation
                # across EVERY adjacent table pair.
                # ==============================================

                trace.mark("semantic_path_validation")

                (
                    physical_path,
                    semantic_chain,
                ) = (
                    self
                    ._select_semantically_valid_path(
                        candidate_paths=
                            candidate_paths,

                        trace=
                            trace,

                        counters=
                            counters,
                    )
                )

                if (
                    physical_path is None
                    or not semantic_chain
                ):

                    raise ValueError(
                        "No semantically valid physical "
                        "path found between "
                        f"{source_concept} and "
                        f"{target_concept}"
                    )

                # ==============================================
                # Preserve every semantic bridge relationship.
                # ==============================================

                for relationship in (
                    semantic_chain
                ):

                    self._append_unique_semantic_relationship(
                        semantic_relationships,
                        relationship,
                    )

                physical_paths.append(
                    physical_path
                )

                # ==============================================
                # Add bridge-table metadata dynamically.
                #
                # Requested concepts remain untouched.
                #
                # Example:
                #
                # requested:
                #   A, C
                #
                # physical path:
                #   A -> B -> C
                #
                # GraphPlan.tables should still know about B.
                #
                # B is discovered entirely from ontology
                # metadata; it is never hard-coded.
                # ==============================================

                trace.mark("bridge_table_resolution")

                self._add_bridge_tables(
                    resolved_tables=
                        resolved_tables,

                    semantic_chain=
                        semantic_chain,

                    counters=
                        counters,
                )

            # ==================================================
            # 4. Resolve existence branches
            #
            # Each existence condition is independently planned
            # from the result concept to the existence target.
            #
            # The complete physical path must be semantically
            # authorized across every adjacent table pair.
            # ==================================================

            if existence_conditions:

                trace.mark("existence_branch_planning")

                anchor_concept = (
                    semantic_query
                    .result_concept
                )

                anchor_table = (
                    concept_to_table.get(
                        anchor_concept
                    )
                )

                if anchor_table is None:

                    raise ValueError(
                        "Unable to resolve existence anchor "
                        f"table for concept: {anchor_concept}"
                    )

                candidate_branches: list[
                    GraphExistenceBranch
                ] = []

                seen_targets = set()

                for condition in (
                    existence_conditions
                ):

                    target_concept = (
                        condition.concept
                    )

                    if target_concept in seen_targets:
                        continue

                    seen_targets.add(
                        target_concept
                    )

                    target_table = (
                        concept_to_table.get(
                            target_concept
                        )
                    )

                    if target_table is None:

                        raise ValueError(
                            "Unable to resolve existence "
                            "target table for concept: "
                            f"{target_concept}"
                        )

                    if (
                        target_table.key
                        == anchor_table.key
                    ):

                        raise ValueError(
                            "Existence target cannot be "
                            "the same table as the result "
                            "anchor."
                        )

                    with trace.stage(
                        "existence_path_lookup",
                        mark=False,
                    ):

                        counters[
                            "physical_path_lookups"
                        ] += 1

                        candidate_paths = (
                            self._ontology
                            .find_physical_paths(
                                start_table_key=
                                    anchor_table.key,

                                end_table_key=
                                    target_table.key,
                            )
                        )

                    counters[
                        "candidate_paths"
                    ] += len(
                        candidate_paths
                    )

                    if not candidate_paths:

                        raise ValueError(
                            "No candidate physical path "
                            "found for existence branch "
                            f"{anchor_concept} -> "
                            f"{target_concept}"
                        )

                    (
                        selected_path,
                        semantic_chain,
                    ) = (
                        self
                        ._select_semantically_valid_path(
                            candidate_paths=
                                candidate_paths,

                            trace=
                                trace,

                            counters=
                                counters,
                        )
                    )

                    if (
                        selected_path is None
                        or not semantic_chain
                    ):

                        raise ValueError(
                            "No semantically valid path "
                            "found for existence branch "
                            f"{anchor_concept} -> "
                            f"{target_concept}"
                        )

                    self._add_bridge_tables(
                        resolved_tables=
                            resolved_tables,

                        semantic_chain=
                            semantic_chain,

                        counters=
                            counters,
                    )

                    candidate_branches.append(
                        GraphExistenceBranch(
                            anchor_concept=
                                anchor_concept,

                            target_concept=
                                target_concept,

                            anchor_table_key=
                                anchor_table.key,

                            target_table_key=
                                target_table.key,

                            semantic_relationships=
                                semantic_chain,

                            physical_path=
                                selected_path,
                        )
                    )

                # ----------------------------------------------
                # Remove existence targets already implied by
                # a longer existence branch.
                #
                # Example:
                #
                # Customer -> Invoice
                # Customer -> Invoice -> Claim
                #
                # Invoice existence is subsumed by Claim.
                # ----------------------------------------------

                for branch in candidate_branches:

                    subsumed = False

                    for other in candidate_branches:

                        if branch is other:
                            continue

                        if (
                            branch.target_table_key
                            in other
                            .physical_path
                            .tables[1:-1]
                        ):

                            subsumed = True
                            break

                    if not subsumed:

                        existence_branches.append(
                            branch
                        )

                counters[
                    "existence_branches"
                ] = len(
                    existence_branches
                )

            # ==================================================
            # 5. Construct GraphPlan
            # ==================================================

            with trace.stage("graph_plan_build"):

                result = GraphPlan(
                    # IMPORTANT:
                    #
                    # concepts represents the business concepts
                    # requested by the semantic layer.
                    #
                    # Bridge concepts discovered by the planner
                    # are represented through:
                    #
                    # - semantic_relationships
                    # - tables
                    # - physical_paths
                    #
                    # They are NOT injected back into the original
                    # user semantic request.
                    concepts=
                        concepts,

                    tables=
                        resolved_tables,

                    semantic_relationships=
                        semantic_relationships,

                    physical_paths=
                        physical_paths,

                    existence_branches=
                        existence_branches,
                )

            return result

    # ======================================================
    # Direct path selection
    # ======================================================

    def _select_best_path(
        self,
        candidate_paths: list[
            PhysicalPath
        ],
        trace: PerfTrace,
        counters: dict[
            str,
            int,
        ],
    ) -> PhysicalPath | None:

        counters[
            "ranking_calls"
        ] += 1

        with trace.stage(
            "path_ranking",
            mark=False,
        ):

            result = (
                self._path_ranker
                .select_best(
                    candidate_paths
                )
            )

        return result

    # ======================================================
    # Semantic multi-hop candidate selection
    # ======================================================

    def _select_semantically_valid_path(
        self,
        candidate_paths: list[
            PhysicalPath
        ],
        trace: PerfTrace,
        counters: dict[
            str,
            int,
        ],
    ) -> tuple[
        PhysicalPath | None,
        list[SemanticRelationship],
    ]:
        """
        Select the highest-ranked physical path whose
        complete table chain is semantically authorized.

        Algorithm:

            rank best remaining physical candidate
                ↓
            validate every adjacent table pair
                ↓
            valid?
                YES -> return it
                NO  -> remove it and try the next
        """

        remaining = list(
            candidate_paths
        )

        while remaining:

            candidate = (
                self._select_best_path(
                    candidate_paths=
                        remaining,

                    trace=
                        trace,

                    counters=
                        counters,
                )
            )

            if candidate is None:

                return (
                    None,
                    [],
                )

            counters[
                "semantic_path_candidates_checked"
            ] += 1

            with trace.stage(
                "semantic_path_validation",
                mark=False,
            ):

                semantic_chain = (
                    self._validate_semantic_path(
                        physical_path=
                            candidate,

                        counters=
                            counters,
                    )
                )

            # ----------------------------------------------
            # Complete semantic authorization succeeded.
            # ----------------------------------------------

            if semantic_chain is not None:

                return (
                    candidate,
                    semantic_chain,
                )

            # ----------------------------------------------
            # Candidate is physically valid but not
            # semantically authorized.
            #
            # Remove exactly that physical candidate and
            # try the next PathRanker choice.
            # ----------------------------------------------

            rejected_signature = (
                self._physical_path_signature(
                    candidate
                )
            )

            remaining = [
                path

                for path
                in remaining

                if (
                    self._physical_path_signature(
                        path
                    )
                    != rejected_signature
                )
            ]

        return (
            None,
            [],
        )

    # ======================================================
    # Semantic path validation
    # ======================================================

    def _validate_semantic_path(
        self,
        physical_path: PhysicalPath,
        counters: dict[
            str,
            int,
        ],
    ) -> list[
        SemanticRelationship
    ] | None:
        """
        Validate every adjacent physical table pair against
        the semantic ontology.

        Physical JOIN connectivity does not itself prove
        business-semantic validity.
        """

        tables = (
            physical_path.tables
        )

        if len(tables) < 2:

            return []

        relationships: list[
            SemanticRelationship
        ] = []

        for index in range(
            len(tables) - 1
        ):

            first_table_key = (
                tables[
                    index
                ]
            )

            second_table_key = (
                tables[
                    index + 1
                ]
            )

            counters[
                "semantic_bridge_lookups"
            ] += 1

            relationship = (
                self._ontology
                .find_semantic_relationship_between_tables(
                    first_table_key=
                        first_table_key,

                    second_table_key=
                        second_table_key,
                )
            )

            if relationship is None:

                return None

            relationships.append(
                relationship
            )

        return relationships

    # ======================================================
    # Bridge-table metadata
    # ======================================================

    def _add_bridge_tables(
        self,
        resolved_tables: list[
            TableMetadata
        ],
        semantic_chain: list[
            SemanticRelationship
        ],
        counters: dict[
            str,
            int,
        ],
    ) -> None:
        """
        Add TableMetadata for semantic bridge concepts that
        were not explicitly present in analysis.concepts.

        No bridge concept is hard-coded.
        """

        existing_keys = {
            table.key
            for table
            in resolved_tables
        }

        candidates: list[
            tuple[
                str,
                str,
            ]
        ] = []

        for relationship in (
            semantic_chain
        ):

            candidates.append(
                (
                    relationship.source_table_key,
                    relationship.source_concept,
                )
            )

            candidates.append(
                (
                    relationship.target_table_key,
                    relationship.target_concept,
                )
            )

        for (
            expected_table_key,
            concept,
        ) in candidates:

            if (
                expected_table_key
                in existing_keys
            ):

                continue

            counters[
                "table_lookups"
            ] += 1

            table = (
                self._ontology
                .resolve_table(
                    concept
                )
            )

            if table is None:

                raise ValueError(
                    "Unable to resolve bridge ontology "
                    f"concept: {concept}"
                )

            # ----------------------------------------------
            # Defensive ontology consistency check.
            # ----------------------------------------------

            if (
                table.key
                != expected_table_key
            ):

                raise ValueError(
                    "Bridge semantic relationship and "
                    "Concept -> Table resolution disagree "
                    f"for concept: {concept}"
                )

            resolved_tables.append(
                table
            )

            existing_keys.add(
                table.key
            )

    # ======================================================
    # Semantic relationship deduplication
    # ======================================================

    @staticmethod
    def _append_unique_semantic_relationship(
        relationships: list[
            SemanticRelationship
        ],
        candidate: SemanticRelationship,
    ) -> None:

        candidate_signature = (
            candidate.source_table_key,
            candidate.relationship_type,
            candidate.target_table_key,
        )

        for existing in relationships:

            existing_signature = (
                existing.source_table_key,
                existing.relationship_type,
                existing.target_table_key,
            )

            if (
                existing_signature
                == candidate_signature
            ):

                return

        relationships.append(
            candidate
        )

    # ======================================================
    # Physical path signature
    # ======================================================

    @staticmethod
    def _physical_path_signature(
        physical_path: PhysicalPath,
    ) -> tuple:

        return (
            tuple(
                physical_path.tables
            ),

            tuple(
                (
                    join.source_table,
                    join.target_table,
                    join.source_column,
                    join.target_column,
                    join.join_condition,
                    join.validation_status,
                )

                for join
                in physical_path.joins
            ),
        )

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