from nlu.models import QuestionAnalysis

from planning.graph_models import GraphPlan
from planning.grain_models import GrainDecision


class GrainResolver:
    """
    Resolve query grain dynamically from structured
    business intent and ontology-resolved concepts.

    IMPORTANT:

    This class contains NO table-specific rules.

    It does not know that:

        Customer = customer table
        Invoice = invoice table
        PaymentReceipt = payment table

    All physical resolution comes from GraphPlan.

    Adding a new ontology concept therefore does not
    require changing this class.
    """

    def resolve(
        self,
        analysis: QuestionAnalysis,
        graph_plan: GraphPlan,
    ) -> GrainDecision:

        if not graph_plan.concepts:
            raise ValueError(
                "Cannot resolve grain because "
                "GraphPlan contains no concepts."
            )

        if not graph_plan.tables:
            raise ValueError(
                "Cannot resolve grain because "
                "GraphPlan contains no tables."
            )

        # ==================================================
        # Build dynamic Concept -> Table lookup
        # ==================================================

        concept_to_table = {
            table.business_concept: table
            for table in graph_plan.tables
            if table.business_concept
        }

        # ==================================================
        # Validate NLU references
        # ==================================================

        self._validate_analysis_concepts(
            analysis=analysis,
            available_concepts=set(
                concept_to_table.keys()
            ),
        )

        # ==================================================
        # Determine final result grain
        #
        # Explicit GROUP BY takes priority.
        #
        # Otherwise result_concept determines the
        # logical answer grain.
        # ==================================================

        if analysis.group_by_concepts:

            result_concepts = (
                self._unique_preserve_order(
                    analysis.group_by_concepts
                )
            )

            result_reason = (
                "Explicit business grouping supplied "
                "by QuestionAnalysis."
            )

        elif analysis.result_concept:

            result_concepts = [
                analysis.result_concept
            ]

            result_reason = (
                "QuestionAnalysis explicitly identified "
                "the result business concept."
            )

        else:

            raise ValueError(
                "QuestionAnalysis does not specify "
                "result_concept or group_by_concepts."
            )

        # ==================================================
        # Resolve result tables dynamically
        # ==================================================

        result_table_keys = []

        for concept in result_concepts:

            table = concept_to_table.get(
                concept
            )

            if table is None:

                raise ValueError(
                    "No ontology table resolved for "
                    f"result concept: {concept}"
                )

            result_table_keys.append(
                table.key
            )

        # ==================================================
        # Resolve measure grain dynamically
        # ==================================================

        measure_concept = (
            analysis.measure_concept
        )

        measure_table_key = None

        if measure_concept:

            measure_table = (
                concept_to_table.get(
                    measure_concept
                )
            )

            if measure_table is None:

                raise ValueError(
                    "No ontology table resolved for "
                    f"measure concept: "
                    f"{measure_concept}"
                )

            measure_table_key = (
                measure_table.key
            )

        # ==================================================
        # Resolve working grain
        #
        # Aggregate / count:
        # calculation occurs at measure grain.
        #
        # Non-aggregate:
        # calculation operates at final result grain
        # when there is one result concept.
        #
        # Multiple result concepts represent a composite
        # result grain. No artificial single working
        # concept is invented.
        # ==================================================

        working_concept = None
        working_table_key = None

        if (
            analysis.aggregation is not None
            and measure_concept is not None
        ):

            working_concept = (
                measure_concept
            )

            working_table_key = (
                measure_table_key
            )

            working_reason = (
                "Aggregation operates on rows belonging "
                "to the measure business concept."
            )

        elif len(result_concepts) == 1:

            working_concept = (
                result_concepts[0]
            )

            working_table_key = (
                result_table_keys[0]
            )

            working_reason = (
                "No separate measure grain is required; "
                "the result business concept defines "
                "the working grain."
            )

        else:

            working_reason = (
                "The result has a composite business "
                "grain; no single working concept is "
                "invented at this stage."
            )

        # ==================================================
        # Relationship/cardinality validation
        #
        # We do NOT hard-code concepts here.
        #
        # We only confirm that ontology semantic
        # relationships are available when result and
        # measure concepts differ.
        # ==================================================

        relationship_reason = (
            self._describe_measure_relationship(
                result_concepts=
                    result_concepts,

                measure_concept=
                    measure_concept,

                graph_plan=
                    graph_plan,
            )
        )

        reason = (
            f"{result_reason} "
            f"{working_reason} "
            f"{relationship_reason}"
        ).strip()

        return GrainDecision(
            result_concepts=
                result_concepts,

            result_table_keys=
                result_table_keys,

            working_concept=
                working_concept,

            working_table_key=
                working_table_key,

            measure_concept=
                measure_concept,

            measure_table_key=
                measure_table_key,

            aggregation=
                analysis.aggregation,

            reason=
                reason,
        )

    # ======================================================
    # Analysis validation
    # ======================================================

    @staticmethod
    def _validate_analysis_concepts(
        analysis: QuestionAnalysis,
        available_concepts: set[str],
    ) -> None:

        referenced_concepts = set(
            analysis.concepts
        )

        if analysis.result_concept:
            referenced_concepts.add(
                analysis.result_concept
            )

        if analysis.measure_concept:
            referenced_concepts.add(
                analysis.measure_concept
            )

        referenced_concepts.update(
            analysis.group_by_concepts
        )

        for entity in analysis.entities:
            referenced_concepts.add(
                entity.concept
            )

        unresolved = (
            referenced_concepts
            - available_concepts
        )

        if unresolved:

            raise ValueError(
                "GrainResolver received concepts "
                "that were not resolved by GraphPlanner: "
                f"{sorted(unresolved)}"
            )

    # ======================================================
    # Semantic cardinality explanation / validation
    # ======================================================

    @staticmethod
    def _describe_measure_relationship(
        result_concepts: list[str],
        measure_concept: str | None,
        graph_plan: GraphPlan,
    ) -> str:

        if measure_concept is None:
            return (
                "No measure concept was requested."
            )

        if measure_concept in result_concepts:
            return (
                "Measure and result use the same "
                "business concept."
            )

        matching_relationships = []

        result_set = set(
            result_concepts
        )

        for relationship in (
            graph_plan.semantic_relationships
        ):

            source = (
                relationship.source_concept
            )

            target = (
                relationship.target_concept
            )

            if (
                source in result_set
                and target == measure_concept
            ):
                matching_relationships.append(
                    relationship
                )

            elif (
                target in result_set
                and source == measure_concept
            ):
                matching_relationships.append(
                    relationship
                )

        if not matching_relationships:

            return (
                "Result and measure concepts are "
                "connected through the resolved graph; "
                "no direct semantic edge is required "
                "by GrainResolver."
            )

        descriptions = []

        for relationship in matching_relationships:

            descriptions.append(
                (
                    f"{relationship.source_concept} "
                    f"--{relationship.relationship_type}--> "
                    f"{relationship.target_concept} "
                    f"[{relationship.cardinality}]"
                )
            )

        return (
            "Semantic cardinality context: "
            + "; ".join(descriptions)
            + "."
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