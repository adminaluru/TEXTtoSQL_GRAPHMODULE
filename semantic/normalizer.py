import re

from nlu.models import (
    QuestionAnalysis,
)

from ontology.concept_catalog import (
    BusinessConceptItem,
    ConceptCatalogService,
)

from semantic.models import (
    AggregationIntent,
    ExistenceIntent,
    GroupingIntent,
    SemanticFilter,
    SemanticNormalizationResult,
    SemanticQuery,
)


class SemanticQueryNormalizer:
    """
    Deterministic trust boundary between:

        probabilistic LLM interpretation

    and:

        deterministic query planning

    Responsibilities:

    - canonicalize ontology concepts
    - normalize supported query semantics
    - normalize entity GROUP BY semantics
    - detect contradictions
    - enforce the semantic capability allowlist
    - fail closed when meaning is unsupported
    - never generate physical database decisions

    This component does NOT:

    - generate SQL
    - resolve physical tables
    - resolve physical columns
    - choose grouping columns
    - choose display columns
    - choose joins
    - choose COUNT DISTINCT because of join fan-out
    - perform database execution
    - call an LLM
    - implement tenant/security policy
    """

    # ======================================================
    # Semantic capability contract — v1
    # ======================================================

    SUPPORTED_AGGREGATIONS = {
        "COUNT",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
    }

    SUPPORTED_QUERY_TYPES = {
        "aggregate",
        "count",
        "list",
    }

    MAX_CONCEPTS = 5
    MAX_FILTERS = 10
    MAX_AGGREGATIONS = 3
    MAX_EXISTENCE_CONDITIONS = 4

    def __init__(
        self,
        concept_catalog:
            ConceptCatalogService,
    ) -> None:

        self._concept_catalog = (
            concept_catalog
        )

    # ======================================================
    # Public API
    # ======================================================

    def normalize(
        self,
        analysis: QuestionAnalysis,
    ) -> SemanticNormalizationResult:

        # ==================================================
        # 1. Load live ontology vocabulary
        # ==================================================

        catalog = (
            self._concept_catalog
            .list_business_concepts()
        )

        if not catalog:

            return self._invalid(
                reason_code=
                    "UNKNOWN_CONCEPT",

                message=(
                    "Ontology business concept "
                    "catalog is empty."
                ),
            )

        lookup = (
            self._build_concept_lookup(
                catalog
            )
        )

        # ==================================================
        # 2. Capability limits
        # ==================================================

        if (
            len(analysis.concepts)
            > self.MAX_CONCEPTS
        ):

            return self._unsupported(
                reason_code=
                    "SEMANTIC_CONTRADICTION",

                message=(
                    "Query contains more business "
                    "concepts than semantic v1 "
                    "supports."
                ),
            )

        if (
            len(analysis.entities)
            > self.MAX_FILTERS
        ):

            return self._unsupported(
                reason_code=
                    "SEMANTIC_CONTRADICTION",

                message=(
                    "Query contains more filters "
                    "than semantic v1 supports."
                ),
            )

        # ==================================================
        # 3. Canonicalize core concepts
        # ==================================================

        try:

            concepts = (
                self._canonicalize_concepts(
                    analysis.concepts,
                    lookup,
                )
            )

            result_concept = (
                self._canonicalize_optional(
                    analysis.result_concept,
                    lookup,
                )
            )

            measure_concept = (
                self._canonicalize_optional(
                    analysis.measure_concept,
                    lookup,
                )
            )

        except ValueError as exc:

            return self._invalid(
                reason_code=
                    "UNKNOWN_CONCEPT",

                message=str(exc),
            )

        # ==================================================
        # 4. Cross-field concept invariants
        # ==================================================

        if (
            result_concept
            and result_concept
            not in concepts
        ):

            return self._invalid(
                reason_code=
                    "RESULT_CONCEPT_NOT_IN_QUERY",

                message=(
                    "result_concept must also "
                    "exist in concepts."
                ),
            )

        if (
            measure_concept
            and measure_concept
            not in concepts
        ):

            return self._invalid(
                reason_code=
                    "MEASURE_CONCEPT_NOT_IN_QUERY",

                message=(
                    "measure_concept must also "
                    "exist in concepts."
                ),
            )

        # ==================================================
        # 5. Canonical GROUP BY semantics
        #
        # Current raw QuestionAnalysis exposes only:
        #
        #     group_by_concepts
        #
        # Therefore the only grouping semantics we can
        # safely infer here are ENTITY groupings.
        #
        # Example:
        #
        #     group_by_concepts=["Customer"]
        #
        # becomes:
        #
        #     GroupingIntent(
        #         target_kind="entity",
        #         concept="Customer",
        #         attribute=None,
        #     )
        #
        # We do NOT choose:
        #
        #     Customer.id
        #     Customer.name
        #
        # here. Those are physical planning decisions.
        # ==================================================

        groupings = []

        if analysis.group_by_concepts:

            try:

                groups = (
                    self._canonicalize_concepts(
                        analysis
                        .group_by_concepts,
                        lookup,
                    )
                )

            except ValueError as exc:

                return self._invalid(
                    reason_code=
                        "UNKNOWN_CONCEPT",

                    message=str(exc),
                )

            for concept in groups:

                if concept not in concepts:

                    return self._invalid(
                        reason_code=
                            "GROUP_BY_CONCEPT_NOT_IN_QUERY",

                        message=(
                            "Every group-by concept "
                            "must exist in concepts."
                        ),
                    )

                groupings.append(
                    GroupingIntent(
                        target_kind=
                            "entity",

                        concept=
                            concept,

                        attribute=
                            None,
                    )
                )

        # ==================================================
        # 6. Query type allowlist
        # ==================================================

        intent = (
            analysis.intent
            .strip()
            .lower()
        )

        if (
            intent
            not in self
            .SUPPORTED_QUERY_TYPES
        ):

            return self._unsupported(
                reason_code=
                    "UNSUPPORTED_QUERY_TYPE",

                message=(
                    f"Query intent '{analysis.intent}' "
                    "is not enabled in "
                    "SemanticQuery v1."
                ),
            )

        # ==================================================
        # 7. Filters
        #
        # Current NLU EntityMention has no operator field.
        # Therefore SemanticQuery v1 accepts its existing
        # equality-filter semantics.
        #
        # A later deterministic layer may apply a more
        # specific physical matching policy.
        # ==================================================

        filters = []

        for entity in analysis.entities:

            try:

                filter_concept = (
                    self._canonicalize_required(
                        entity.concept,
                        lookup,
                    )
                )

            except ValueError as exc:

                return self._invalid(
                    reason_code=
                        "UNKNOWN_CONCEPT",

                    message=str(exc),
                )

            if (
                filter_concept
                not in concepts
            ):

                return self._invalid(
                    reason_code=
                        "FILTER_CONCEPT_NOT_IN_QUERY",

                    message=(
                        "Filter concept must also "
                        "exist in concepts."
                    ),
                )

            attribute = (
                entity.attribute.strip()
            )

            if not attribute:

                return self._invalid(
                    reason_code=
                        "SEMANTIC_CONTRADICTION",

                    message=(
                        "Filter attribute cannot "
                        "be empty."
                    ),
                )

            filters.append(
                SemanticFilter(
                    concept=
                        filter_concept,

                    attribute=
                        attribute,

                    operator=
                        "=",

                    value=
                        entity.value,
                )
            )

        # ==================================================
        # 8. Canonical existence semantics
        #
        # Current raw QuestionAnalysis does not yet expose
        # an explicit existence field.
        #
        # For LIST queries, semantic v1 therefore applies a
        # deterministic rule:
        #
        #     result_concept
        #         -> entity being returned
        #
        #     concepts used by entity filters
        #         -> filtering context
        #
        #     remaining non-result concepts
        #         -> required related-entity existence
        #
        # Examples:
        #
        # GQ003:
        #
        #     concepts:
        #         Customer, Invoice
        #
        #     result:
        #         Invoice
        #
        #     Customer participates in a filter
        #
        #     => no existence condition
        #
        # GQ006:
        #
        #     concepts:
        #         Invoice, BrokenPromiseToPay
        #
        #     result:
        #         Invoice
        #
        #     BrokenPromiseToPay is neither the result
        #     concept nor a filter concept
        #
        #     => ExistenceIntent(
        #            concept="BrokenPromiseToPay"
        #        )
        #
        # This layer still makes NO physical path or SQL
        # decision.
        # ==================================================

        existence_conditions = []

        if intent == "list":

            filter_concepts = {
                item.concept
                for item in filters
            }

            for concept in concepts:

                if (
                    concept == result_concept
                    or concept in filter_concepts
                ):
                    continue

                existence_conditions.append(
                    ExistenceIntent(
                        concept=concept
                    )
                )

            if (
                len(existence_conditions)
                > self.MAX_EXISTENCE_CONDITIONS
            ):

                return self._unsupported(
                    reason_code=
                        "SEMANTIC_CONTRADICTION",

                    message=(
                        "LIST query contains more "
                        "existence conditions than "
                        "SemanticQuery v1 supports."
                    ),
                )

        # ==================================================
        # 9. LIST
        #
        # SemanticQuery v1 LIST contract:
        #
        #     query_type = "list"
        #     result_concept = <business entity>
        #
        # The semantic layer deliberately does NOT choose
        # physical/default projection columns.
        #
        # Bounded default projections are resolved later
        # from ontology metadata:
        #
        #     projection_role = "default"
        #     projection_order = <n>
        # ==================================================

        if intent == "list":

            if not result_concept:

                return self._invalid(
                    reason_code=
                        "SEMANTIC_CONTRADICTION",

                    message=(
                        "LIST query requires a "
                        "result_concept."
                    ),
                )

            if groupings:

                return self._unsupported(
                    reason_code=
                        "UNSUPPORTED_GROUP_BY",

                    message=(
                        "LIST + GROUP BY is not enabled "
                        "in SemanticQuery v1."
                    ),
                )

            if analysis.aggregation is not None:

                return self._invalid(
                    reason_code=
                        "SEMANTIC_CONTRADICTION",

                    message=(
                        "LIST query cannot contain an "
                        "aggregation function."
                    ),
                )

            if (
                measure_concept is not None
                or (
                    analysis.measure_attribute
                    is not None
                    and analysis
                    .measure_attribute
                    .strip()
                )
            ):

                return self._invalid(
                    reason_code=
                        "SEMANTIC_CONTRADICTION",

                    message=(
                        "LIST query cannot contain "
                        "aggregate measure semantics."
                    ),
                )

            query = SemanticQuery(
                query_type=
                    "list",

                concepts=
                    concepts,

                result_concept=
                    result_concept,

                aggregations=
                    [],

                groupings=
                    [],

                existence_conditions=
                    existence_conditions,

                filters=
                    filters,
            )

            return SemanticNormalizationResult(
                status="ready",
                query=query,
            )

        # ==================================================
        # 10. Aggregation
        # ==================================================

        aggregation = (
            analysis.aggregation
        )

        if not aggregation:

            return self._invalid(
                reason_code=
                    "MISSING_AGGREGATE_TARGET",

                message=(
                    "Aggregate/count query requires "
                    "an aggregation function."
                ),
            )

        function = (
            aggregation
            .strip()
            .upper()
        )

        if (
            function
            not in self
            .SUPPORTED_AGGREGATIONS
        ):

            return self._unsupported(
                reason_code=
                    "UNSUPPORTED_QUERY_TYPE",

                message=(
                    f"Aggregation '{function}' "
                    "is not supported."
                ),
            )

        aggregate_result = (
            self._normalize_aggregation(
                function=
                    function,

                measure_concept=
                    measure_concept,

                measure_attribute=
                    analysis
                    .measure_attribute,

                result_concept=
                    result_concept,

                intent=
                    intent,
            )
        )

        if isinstance(
            aggregate_result,
            SemanticNormalizationResult,
        ):

            return aggregate_result

        aggregations = [
            aggregate_result
        ]

        if (
            len(aggregations)
            > self.MAX_AGGREGATIONS
        ):

            return self._unsupported(
                reason_code=
                    "SEMANTIC_CONTRADICTION",

                message=(
                    "Too many aggregations for "
                    "SemanticQuery v1."
                ),
            )

        # ==================================================
        # 11. READY
        # ==================================================

        query = SemanticQuery(
            query_type=
                "aggregate",

            concepts=
                concepts,

            result_concept=
                result_concept,

            aggregations=
                aggregations,

            groupings=
                groupings,

            filters=
                filters,
        )

        return SemanticNormalizationResult(
            status="ready",
            query=query,
        )

    # ======================================================
    # Aggregation normalization
    # ======================================================

    def _normalize_aggregation(
        self,
        function: str,
        measure_concept: str | None,
        measure_attribute: str | None,
        result_concept: str | None,
        intent: str,
    ) -> (
        AggregationIntent
        | SemanticNormalizationResult
    ):

        # ==================================================
        # COUNT v1
        #
        # SemanticQuery v1 supports ENTITY COUNT only.
        #
        # We deliberately do NOT support:
        #
        # COUNT(attribute)
        # COUNT(DISTINCT attribute)
        # explicit user DISTINCT
        #
        # yet.
        # ==================================================

        if function == "COUNT":

            concept = (
                measure_concept
                or result_concept
            )

            if not concept:

                return self._invalid(
                    reason_code=
                        "MISSING_AGGREGATE_TARGET",

                    message=(
                        "COUNT requires a business "
                        "entity concept."
                    ),
                )

            attribute = (
                measure_attribute.strip()
                if measure_attribute
                else None
            )

            if (
                attribute
                and not self._looks_like_count_phrase(
                    attribute=attribute,
                    concept=concept,
                )
            ):

                return self._unsupported(
                    reason_code=
                        "UNSUPPORTED_ATTRIBUTE_COUNT",

                    message=(
                        "SemanticQuery v1 supports "
                        "COUNT of entities only. "
                        f"Attribute COUNT target "
                        f"'{attribute}' is not enabled."
                    ),
                )

            return AggregationIntent(
                function="COUNT",

                target_kind=
                    "entity",

                concept=
                    concept,

                attribute=
                    None,

                distinct_requested=
                    False,
            )

        # ==================================================
        # SUM / AVG / MIN / MAX
        #
        # These require a real business attribute.
        # ==================================================

        if function in {
            "SUM",
            "AVG",
            "MIN",
            "MAX",
        }:

            if not measure_concept:

                return self._invalid(
                    reason_code=
                        "MISSING_AGGREGATE_TARGET",

                    message=(
                        f"{function} requires a "
                        "measure concept."
                    ),
                )

            if (
                not measure_attribute
                or not measure_attribute.strip()
            ):

                return self._invalid(
                    reason_code=
                        "MISSING_ATTRIBUTE_TARGET",

                    message=(
                        f"{function} requires a "
                        "business attribute target."
                    ),
                )

            return AggregationIntent(
                function=
                    function,

                target_kind=
                    "attribute",

                concept=
                    measure_concept,

                attribute=
                    measure_attribute.strip(),

                distinct_requested=
                    False,
            )

        return self._unsupported(
            reason_code=
                "UNSUPPORTED_QUERY_TYPE",

            message=(
                f"Aggregation '{function}' "
                "is not supported."
            ),
        )

    # ======================================================
    # COUNT phrase canonicalization
    # ======================================================

    def _looks_like_count_phrase(
        self,
        attribute: str,
        concept: str,
    ) -> bool:

        attribute_norm = (
            self._normalize_text(
                attribute
            )
        )

        concept_norm = (
            self._normalize_text(
                concept
            )
        )

        concept_tokens = (
            concept_norm.split()
        )

        if attribute_norm in {
            "count",
            "record count",
            "records count",
        }:

            return True

        if attribute_norm == (
            concept_norm
            + " count"
        ):

            return True

        singular_last = (
            concept_tokens[-1]
            if concept_tokens
            else concept_norm
        )

        plural_last = (
            singular_last
            if singular_last.endswith("s")
            else singular_last + "s"
        )

        accepted = {
            f"{singular_last} count",
            f"{plural_last} count",

            f"count of {singular_last}",
            f"count of {plural_last}",

            f"number of {singular_last}",
            f"number of {plural_last}",
        }

        return (
            attribute_norm
            in accepted
        )

    # ======================================================
    # Ontology concept handling
    # ======================================================

    @staticmethod
    def _build_concept_lookup(
        catalog: list[
            BusinessConceptItem
        ],
    ) -> dict[str, str]:

        return {
            item.concept
            .strip()
            .lower():
                item.concept

            for item in catalog
        }

    def _canonicalize_concepts(
        self,
        concepts: list[str],
        lookup: dict[str, str],
    ) -> list[str]:

        result = []
        seen = set()

        for concept in concepts:

            canonical = (
                self._canonicalize_required(
                    concept,
                    lookup,
                )
            )

            if canonical not in seen:

                seen.add(
                    canonical
                )

                result.append(
                    canonical
                )

        return result

    @staticmethod
    def _canonicalize_optional(
        concept: str | None,
        lookup: dict[str, str],
    ) -> str | None:

        if not concept:
            return None

        return (
            SemanticQueryNormalizer
            ._canonicalize_required(
                concept,
                lookup,
            )
        )

    @staticmethod
    def _canonicalize_required(
        concept: str,
        lookup: dict[str, str],
    ) -> str:

        key = (
            concept
            .strip()
            .lower()
        )

        canonical = (
            lookup.get(
                key
            )
        )

        if canonical is None:

            raise ValueError(
                "Business concept does not "
                "exist in the ontology: "
                f"{concept}"
            )

        return canonical

    # ======================================================
    # Simple deterministic text normalization
    # ======================================================

    @staticmethod
    def _normalize_text(
        value: str,
    ) -> str:

        value = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1 \2",
            value,
        )

        value = value.lower()

        value = re.sub(
            r"[_\-]+",
            " ",
            value,
        )

        value = re.sub(
            r"[^a-z0-9\s]",
            " ",
            value,
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return value.strip()

    # ======================================================
    # Outcome helpers
    # ======================================================

    @staticmethod
    def _invalid(
        reason_code: str,
        message: str,
    ) -> SemanticNormalizationResult:

        return SemanticNormalizationResult(
            status="invalid",
            reason_code=reason_code,
            message=message,
        )

    @staticmethod
    def _unsupported(
        reason_code: str,
        message: str,
    ) -> SemanticNormalizationResult:

        return SemanticNormalizationResult(
            status="unsupported",
            reason_code=reason_code,
            message=message,
        )