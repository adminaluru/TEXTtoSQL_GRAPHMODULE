import re

from datetime import date
from difflib import SequenceMatcher
from typing import Callable

from nlu.models import QuestionAnalysis

from ontology.models import (
    ColumnMetadata,
)

from ontology.ontology_service import (
    OntologyService,
)

from planning.column_models import (
    ColumnResolution,
    ResolvedEntityGrouping,
    ResolvedFilterColumn,
    ResolvedMeasureColumn,
    ResolvedProjectionColumn,
)

from planning.date_period import business_today, parse_period
from planning.errors import UnsupportedPredicateError

from planning.graph_models import GraphPlan


DATE_FILTER_UNSUPPORTED = (
    "Date and time conditions are not supported yet, so I can't "
    "answer for a specific date or period."
)


class ColumnResolver:
    """
    Dynamically map business attributes from NLU to
    physical PostgreSQL columns stored in Neo4j.

    This resolver contains NO table-specific or
    business-concept-specific mappings.

    Resolution uses:

    - business concept context
    - normalized business attribute
    - concept-prefix stripping
    - Column.name
    - optional Column aliases
    - Column.description
    - Column.category
    - Column.data_type
    - deterministic lexical similarity
    - candidate score separation

    Weak or ambiguous matches fail closed.

    TEMPORARY POC POLICY:

    Name-like string filters use case-insensitive
    partial matching:

        ILIKE '%value%'

    Other filters continue using exact equality.

    Proper Entity Resolution will replace this policy
    in a later production phase.
    """

    # ======================================================
    # Confidence thresholds
    # ======================================================

    MIN_FILTER_SCORE = 85
    MIN_MEASURE_SCORE = 85

    # Even if the highest candidate passes MIN_SCORE,
    # it must be sufficiently better than candidate #2.
    MIN_SCORE_MARGIN = 18

    # Typo / spelling similarity threshold.
    FUZZY_SIMILARITY_THRESHOLD = 0.84

    # Bounded default entity view.
    #
    # Any current/future result concept may define at most
    # this many ontology-approved default projection fields.
    MAX_DEFAULT_PROJECTIONS = 5

    # ======================================================
    # Data-type hints
    # ======================================================

    NUMERIC_TYPE_MARKERS = (
        "int",
        "decimal",
        "numeric",
        "float",
        "double",
        "real",
        "money",
    )

    DATE_TYPE_MARKERS = (
        "date",
        "time",
        "timestamp",
    )

    def __init__(
        self,
        ontology_service: OntologyService,
        today: Callable[[], date] = business_today,
    ) -> None:

        self._ontology = (
            ontology_service
        )

        # "Today" in the business time zone (D-007), injectable
        # so relative periods are testable.
        self._today = today

    # ======================================================
    # Public API
    # ======================================================

    def resolve(
        self,
        analysis: QuestionAnalysis,
        graph_plan: GraphPlan,
    ) -> ColumnResolution:

        concept_to_table = {
            table.business_concept:
                table

            for table
            in graph_plan.tables

            if table.business_concept
        }

        resolved_filters = []

        # ==================================================
        # Filter/entity attributes
        # ==================================================

        for entity in analysis.entities:

            table = concept_to_table.get(
                entity.concept
            )

            if table is None:

                raise ValueError(
                    "No resolved table exists for "
                    f"filter concept: "
                    f"{entity.concept}"
                )

            columns = (
                self._ontology
                .get_table_columns(
                    table.key
                )
            )

            if not columns:

                raise ValueError(
                    "No column metadata found for "
                    f"table: {table.key}"
                )

            column, score = (
                self._resolve_attribute(
                    concept=
                        entity.concept,

                    attribute=
                        entity.attribute,

                    columns=
                        columns,

                    prefer_measure=
                        False,
                )
            )

            if score < self.MIN_FILTER_SCORE:

                raise ValueError(
                    "Unable to confidently resolve "
                    f"business attribute "
                    f"'{entity.attribute}' "
                    f"for concept "
                    f"'{entity.concept}'. "
                    f"Best score: {score}"
                )

            # ==================================================
            # Date / time condition -> exact period (D-007)
            #
            # start <= column < day after end, as two bound
            # parameters. Unclear periods are refused, never
            # guessed and never dropped.
            # ==================================================

            if self._is_temporal(column):

                period = parse_period(
                    entity.value,
                    self._today(),
                )

                note = (
                    f"{entity.attribute.strip().capitalize()}: "
                    f"{period.describe()}."
                )

                for operator, bound in (
                    (">=", period.start_at),
                    ("<", period.end_before),
                ):

                    if bound is None:
                        continue

                    resolved_filters.append(
                        ResolvedFilterColumn(
                            concept=entity.concept,
                            business_attribute=entity.attribute,
                            table_key=table.key,
                            column_key=column.key,
                            column_name=column.name,
                            operator=operator,
                            value=bound,
                            score=score,
                            note=note,
                        )
                    )

                continue

            # ==================================================
            # Temporary POC filter matching policy
            #
            # Example:
            #
            # Customer / customer name / Blue Prism
            #
            # becomes:
            #
            # name ILIKE '%Blue Prism%'
            #
            # This is generic. There is NO Customer-specific
            # or Blue-Prism-specific condition.
            # ==================================================

            (
                filter_operator,
                filter_value,
            ) = self._resolve_filter_match(
                business_attribute=
                    entity.attribute,

                column=
                    column,

                value=
                    entity.value,
            )

            resolved_filters.append(
                ResolvedFilterColumn(
                    concept=
                        entity.concept,

                    business_attribute=
                        entity.attribute,

                    table_key=
                        table.key,

                    column_key=
                        column.key,

                    column_name=
                        column.name,

                    operator=
                        filter_operator,

                    value=
                        filter_value,

                    score=
                        score,
                )
            )

        # ==================================================
        # Aggregate measure
        # ==================================================

        resolved_measure = None

        if (
            analysis.measure_concept
            and analysis.measure_attribute
        ):

            table = concept_to_table.get(
                analysis.measure_concept
            )

            if table is None:

                raise ValueError(
                    "No resolved table exists for "
                    f"measure concept: "
                    f"{analysis.measure_concept}"
                )

            columns = (
                self._ontology
                .get_table_columns(
                    table.key
                )
            )

            if not columns:

                raise ValueError(
                    "No column metadata found for "
                    f"measure table: {table.key}"
                )

            column, score = (
                self._resolve_attribute(
                    concept=
                        analysis.measure_concept,

                    attribute=
                        analysis.measure_attribute,

                    columns=
                        columns,

                    prefer_measure=
                        True,
                )
            )

            if score < self.MIN_MEASURE_SCORE:

                raise ValueError(
                    "Unable to confidently resolve "
                    f"measure attribute "
                    f"'{analysis.measure_attribute}' "
                    f"for concept "
                    f"'{analysis.measure_concept}'. "
                    f"Best score: {score}"
                )

            resolved_measure = (
                ResolvedMeasureColumn(
                    concept=
                        analysis.measure_concept,

                    business_attribute=
                        analysis.measure_attribute,

                    table_key=
                        table.key,

                    column_key=
                        column.key,

                    column_name=
                        column.name,

                    data_type=
                        column.data_type,

                    category=
                        column.category,

                    score=
                        score,
                )
            )

        # ==================================================
        # Default LIST projection resolution
        #
        # LIST queries do not guess physical columns.
        #
        # For the result entity, use only ontology columns
        # explicitly configured as:
        #
        #     projection_role = "default"
        #
        # ordered by:
        #
        #     projection_order
        #
        # This is generic for every current/future business
        # result concept.
        # ==================================================

        resolved_projections = []

        if analysis.intent == "list":

            result_concept = (
                analysis.result_concept
            )

            if not result_concept:

                raise ValueError(
                    "LIST query requires a "
                    "result_concept for default "
                    "projection resolution."
                )

            table = concept_to_table.get(
                result_concept
            )

            if table is None:

                raise ValueError(
                    "No resolved table exists for "
                    "LIST result concept: "
                    f"{result_concept}"
                )

            columns = (
                self._ontology
                .get_table_columns(
                    table.key
                )
            )

            if not columns:

                raise ValueError(
                    "No column metadata found for "
                    "LIST result table: "
                    f"{table.key}"
                )

            resolved_projections = (
                self._resolve_default_projections(
                    concept=
                        result_concept,

                    table_key=
                        table.key,

                    columns=
                        columns,
                )
            )

        # ==================================================
        # Entity GROUP BY resolution
        #
        # Current QuestionAnalysis expresses grouping as
        # business concepts:
        #
        #     group_by_concepts=["Customer"]
        #
        # For every grouped entity, resolve:
        #
        #     identity -> is_primary_key = True
        #     display  -> semantic_role = "display_name"
        #
        # No concept-specific or table-specific mapping
        # is used here.
        # ==================================================

        resolved_groupings = []

        for concept in (
            self._unique_preserve_order(
                analysis.group_by_concepts
            )
        ):

            table = concept_to_table.get(
                concept
            )

            if table is None:

                raise ValueError(
                    "No resolved table exists for "
                    f"group-by concept: {concept}"
                )

            columns = (
                self._ontology
                .get_table_columns(
                    table.key
                )
            )

            if not columns:

                raise ValueError(
                    "No column metadata found for "
                    f"group-by table: {table.key}"
                )

            resolved_groupings.append(
                self._resolve_entity_grouping(
                    concept=
                        concept,

                    table_key=
                        table.key,

                    columns=
                        columns,
                )
            )

        return ColumnResolution(
            filters=
                resolved_filters,

            measure=
                resolved_measure,

            projections=
                resolved_projections,

            groupings=
                resolved_groupings,
        )

    # ======================================================
    # Default LIST projection resolution
    # ======================================================

    @classmethod
    def _resolve_default_projections(
        cls,
        concept: str,
        table_key: str,
        columns: list[ColumnMetadata],
    ) -> list[ResolvedProjectionColumn]:
        """
        Resolve one bounded default entity projection from
        ontology metadata.

        Required metadata:

            projection_role = "default"
            projection_order = positive integer

        Rules:

        - no concept/table-specific Python mappings
        - no SELECT *
        - no fuzzy guessing
        - explicit, unique ordering is required
        - more than MAX_DEFAULT_PROJECTIONS fails closed
        - missing metadata fails closed
        """

        default_columns = [
            column

            for column
            in columns

            if (
                str(
                    column.projection_role
                    or ""
                )
                .strip()
                .lower()
                == "default"
            )
        ]

        if not default_columns:

            raise ValueError(
                "No default projection metadata exists "
                f"for concept '{concept}' on table "
                f"'{table_key}'."
            )

        if (
            len(default_columns)
            > cls.MAX_DEFAULT_PROJECTIONS
        ):

            raise ValueError(
                "Default projection exceeds bounded "
                f"limit for concept '{concept}'. "
                f"Configured: {len(default_columns)}. "
                f"Maximum: "
                f"{cls.MAX_DEFAULT_PROJECTIONS}."
            )

        invalid_order_columns = [
            column.key

            for column
            in default_columns

            if (
                column.projection_order
                is None
                or column.projection_order < 1
            )
        ]

        if invalid_order_columns:

            raise ValueError(
                "Default projection columns require "
                "positive projection_order metadata. "
                f"Invalid columns: "
                f"{invalid_order_columns}"
            )

        orders = [
            column.projection_order
            for column
            in default_columns
        ]

        if (
            len(orders)
            != len(set(orders))
        ):

            raise ValueError(
                "Default projection contains duplicate "
                "projection_order values for concept "
                f"'{concept}'. Orders: {orders}"
            )

        default_columns.sort(
            key=lambda column: (
                column.projection_order,
                column.ordinal
                if column.ordinal is not None
                else 999999,
                column.name,
            )
        )

        return [
            ResolvedProjectionColumn(
                concept=
                    concept,

                table_key=
                    table_key,

                column_key=
                    column.key,

                column_name=
                    column.name,

                projection_order=
                    column.projection_order,

                semantic_role=
                    column.semantic_role,
            )

            for column
            in default_columns
        ]

    # ======================================================
    # Entity GROUP BY resolution
    # ======================================================

    @staticmethod
    def _resolve_entity_grouping(
        concept: str,
        table_key: str,
        columns: list[ColumnMetadata],
    ) -> ResolvedEntityGrouping:
        """
        Resolve one entity grouping entirely from
        ontology metadata.

        Required ontology contract:

            exactly one:
                is_primary_key = True

            exactly one:
                semantic_role = "display_name"

        The resolver fails closed when identity/display
        metadata is missing or ambiguous.

        There are no rules such as:

            Customer -> id/name
            Invoice  -> id/invoice_num
        """

        identity_columns = [
            column

            for column
            in columns

            if column.is_primary_key
        ]

        if len(identity_columns) != 1:

            candidates = [
                column.key
                for column
                in identity_columns
            ]

            if not identity_columns:

                raise ValueError(
                    "Unable to resolve entity grouping "
                    f"identity for concept '{concept}' "
                    f"on table '{table_key}'. "
                    "No primary-key metadata exists."
                )

            raise ValueError(
                "Unable to resolve entity grouping "
                f"identity for concept '{concept}' "
                f"on table '{table_key}'. "
                "Exactly one primary-key column is "
                "required in the current grouping "
                f"contract. Candidates: {candidates}"
            )

        display_columns = [
            column

            for column
            in columns

            if (
                str(
                    column.semantic_role
                    or ""
                )
                .strip()
                .lower()
                == "display_name"
            )
        ]

        if len(display_columns) != 1:

            candidates = [
                column.key
                for column
                in display_columns
            ]

            if not display_columns:

                raise ValueError(
                    "Unable to resolve entity grouping "
                    f"display column for concept "
                    f"'{concept}' on table "
                    f"'{table_key}'. "
                    "No column has semantic_role="
                    "'display_name'."
                )

            raise ValueError(
                "Unable to resolve entity grouping "
                f"display column for concept "
                f"'{concept}' on table "
                f"'{table_key}'. "
                "Exactly one display_name column is "
                "required. "
                f"Candidates: {candidates}"
            )

        identity = (
            identity_columns[0]
        )

        display = (
            display_columns[0]
        )

        return ResolvedEntityGrouping(
            concept=
                concept,

            table_key=
                table_key,

            identity_column_key=
                identity.key,

            identity_column_name=
                identity.name,

            display_column_key=
                display.key,

            display_column_name=
                display.name,
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

    # ======================================================
    # Temporary POC filter matching
    # ======================================================

    TEMPORAL_TYPES = (
        "TIMESTAMP",
        "DATETIME",
        "DATE",
        "TIME",
    )

    @classmethod
    def _is_temporal(
        cls,
        column,
    ) -> bool:

        data_type = (
            getattr(column, "data_type", None) or ""
        ).upper()

        category = (
            getattr(column, "category", None) or ""
        ).lower()

        return (
            category == "timestamp"
            or data_type.startswith(cls.TEMPORAL_TYPES)
        )

    @classmethod
    def _resolve_filter_match(
        cls,
        business_attribute: str,
        column: ColumnMetadata,
        value,
    ) -> tuple[
        str,
        object,
    ]:
        """
        Literal substring matching for name-like filters.

        Name-like STRING filters:

            name
            customer name
            legal name
            vendor name

        use:

            ILIKE '%value%'

        Other filters remain exact:

            status = 'PAID'
            company_id = 123
            invoice_num = 'INV-001'

        We intentionally do NOT make every text filter
        ILIKE because values such as:

            PAID

        could incorrectly match:

            UNPAID

        Proper EntityResolver / ambiguity handling will
        replace this method later.
        """

        # A date/time condition is a period, not an exact
        # instant: equality against a timestamp silently matches
        # (almost) nothing. Refuse until date ranges have approved
        # boundary and time-zone semantics.
        if cls._is_temporal(column):

            raise UnsupportedPredicateError(
                DATE_FILTER_UNSUPPORTED
            )

        # Non-string values remain exact.
        if not isinstance(
            value,
            str,
        ):

            return (
                "=",
                value,
            )

        normalized_attribute = (
            cls._normalize(
                business_attribute
            )
        )

        normalized_column = (
            cls._normalize(
                column.name
            )
        )

        # Generic name semantics only.
        #
        # No business concept is referenced here.
        is_name_like = (
            normalized_column
            == "name"

            or normalized_attribute
            == "name"

            or normalized_attribute
            .endswith(
                " name"
            )
        )

        if is_name_like:

            cleaned_value = (
                value.strip()
            )

            if not cleaned_value:
                raise ValueError("Name filter cannot be blank.")

            # PostgreSQL LIKE/ILIKE uses backslash as its default
            # escape character. Escape it first, then literal wildcards.
            # Values remain bound SQL parameters, never SQL text.
            escaped_value = (
                cleaned_value
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )

            return (
                "ILIKE",
                f"%{escaped_value}%",
            )

        return (
            "=",
            value,
        )

    # ======================================================
    # Generic attribute resolution
    # ======================================================

    def _resolve_attribute(
        self,
        concept: str,
        attribute: str,
        columns: list[ColumnMetadata],
        prefer_measure: bool,
    ) -> tuple[
        ColumnMetadata,
        int,
    ]:

        variants = (
            self._attribute_variants(
                concept=concept,
                attribute=attribute,
            )
        )

        ranked = []

        for column in columns:

            score = self._score_column(
                variants=variants,
                column=column,
                prefer_measure=
                    prefer_measure,
            )

            ranked.append(
                (
                    score,
                    column,
                )
            )

        if not ranked:

            raise ValueError(
                "No candidate columns available."
            )

        ranked.sort(
            key=lambda item: (
                -item[0],

                item[1].ordinal
                if item[1].ordinal
                is not None
                else 999999,

                item[1].name,
            )
        )

        best_score = ranked[0][0]
        best_column = ranked[0][1]

        # ==================================================
        # Exact score tie
        # ==================================================

        tied = [
            item
            for item
            in ranked
            if item[0] == best_score
        ]

        if len(tied) > 1:

            winner = self._prefer_phrase_as_given(
                attribute=attribute,
                contenders=tied,
                prefer_measure=prefer_measure,
            )

            if winner is not None:
                return winner

            candidates = [
                item[1].key
                for item
                in tied
            ]

            raise ValueError(
                "Ambiguous column resolution for "
                f"'{attribute}' "
                f"under concept '{concept}'. "
                f"Candidates: {candidates}"
            )

        # ==================================================
        # Score margin
        # ==================================================

        if len(ranked) > 1:

            second_score = (
                ranked[1][0]
            )

            margin = (
                best_score
                - second_score
            )

            threshold = (
                self.MIN_MEASURE_SCORE
                if prefer_measure
                else self.MIN_FILTER_SCORE
            )

            if (
                best_score >= threshold
                and second_score >= threshold
                and margin
                < self.MIN_SCORE_MARGIN
            ):

                winner = self._prefer_phrase_as_given(
                    attribute=attribute,
                    contenders=[
                        item
                        for item in ranked
                        if item[0] >= threshold
                        and best_score - item[0]
                        < self.MIN_SCORE_MARGIN
                    ],
                    prefer_measure=prefer_measure,
                )

                if winner is not None:
                    return winner

                candidates = [
                    ranked[0][1].key,
                    ranked[1][1].key,
                ]

                raise ValueError(
                    "Ambiguous column resolution for "
                    f"'{attribute}' "
                    f"under concept '{concept}'. "
                    f"Top candidates: {candidates}. "
                    f"Scores: "
                    f"{best_score}, "
                    f"{second_score}. "
                    f"Margin: {margin}"
                )

        return (
            best_column,
            best_score,
        )

    def _prefer_phrase_as_given(
        self,
        attribute: str,
        contenders: list[tuple[int, ColumnMetadata]],
        prefer_measure: bool,
    ) -> tuple[ColumnMetadata, int] | None:
        """
        Break a near-tie using the attribute exactly as given.

        Variants such as "status" (concept prefix stripped from
        "invoice status") can match several columns equally. The
        phrase the user actually used may still clearly favour one
        ("Invoice lifecycle status"). It decides only with the same
        margin required elsewhere; otherwise the tie stands.
        """

        phrase = self._normalize(attribute)

        scored = sorted(
            (
                (
                    self._score_column(
                        variants={phrase},
                        column=column,
                        prefer_measure=prefer_measure,
                    ),
                    score,
                    column,
                )
                for score, column in contenders
            ),
            key=lambda item: (-item[0], item[2].name),
        )

        if (
            len(scored) > 1
            and scored[0][0] - scored[1][0]
            >= self.MIN_SCORE_MARGIN
        ):
            return (
                scored[0][2],
                scored[0][1],
            )

        return None

    # ======================================================
    # Attribute variants
    # ======================================================

    def _attribute_variants(
        self,
        concept: str,
        attribute: str,
    ) -> set[str]:

        normalized_attribute = (
            self._normalize(
                attribute
            )
        )

        normalized_concept = (
            self._normalize(
                concept
            )
        )

        variants = {
            normalized_attribute
        }

        # --------------------------------------------------
        # Remove complete concept prefix.
        # --------------------------------------------------

        concept_prefix = (
            normalized_concept
            + " "
        )

        if (
            normalized_attribute
            .startswith(
                concept_prefix
            )
        ):

            stripped = (
                normalized_attribute[
                    len(concept_prefix):
                ]
                .strip()
            )

            if stripped:

                variants.add(
                    stripped
                )

        # --------------------------------------------------
        # Remove repeated leading concept tokens.
        # --------------------------------------------------

        concept_tokens = (
            normalized_concept.split()
        )

        attribute_tokens = (
            normalized_attribute.split()
        )

        for token in reversed(
            concept_tokens
        ):

            if (
                attribute_tokens
                and attribute_tokens[0]
                == token
            ):

                candidate = (
                    " ".join(
                        attribute_tokens[1:]
                    )
                    .strip()
                )

                if candidate:

                    variants.add(
                        candidate
                    )

        return {
            variant
            for variant
            in variants
            if variant
        }

    # ======================================================
    # Deterministic scoring
    # ======================================================

    def _score_column(
        self,
        variants: set[str],
        column: ColumnMetadata,
        prefer_measure: bool,
    ) -> int:

        column_name = (
            self._normalize(
                column.name
            )
        )

        description = (
            self._normalize(
                column.description
                or ""
            )
        )

        category = (
            self._normalize(
                column.category
                or ""
            )
        )

        data_type = (
            self._normalize(
                column.data_type
                or ""
            )
        )

        aliases = (
            self._column_aliases(
                column
            )
        )

        candidate_names = {
            column_name,
            *aliases,
        }

        best_lexical_score = 0

        # ==================================================
        # Evaluate every generated business variant
        # ==================================================

        # Each variant is scored on its own (best name match plus its
        # own description evidence) and the best variant wins. A
        # running total across variants made the score depend on set
        # iteration order, i.e. on the process hash seed.
        for requested in variants:

            variant_score = 0

            requested_tokens = set(
                requested.split()
            )

            description_tokens = set(
                description.split()
            )

            for candidate in candidate_names:

                if not candidate:
                    continue

                candidate_tokens = set(
                    candidate.split()
                )

                score = 0

                # ==========================================
                # 1. Exact semantic/physical match
                # ==========================================

                if requested == candidate:

                    score = 140

                # ==========================================
                # 2. Same token set
                # ==========================================

                elif (
                    requested_tokens
                    and requested_tokens
                    == candidate_tokens
                ):

                    score = 130

                # ==========================================
                # 3. Requested phrase contained in candidate
                # ==========================================

                elif (
                    requested_tokens
                    and requested_tokens
                    .issubset(
                        candidate_tokens
                    )
                ):

                    extras = (
                        len(candidate_tokens)
                        - len(requested_tokens)
                    )

                    score = (
                        92
                        - (
                            extras
                            * 14
                        )
                    )

                # ==========================================
                # 4. Candidate contained in request
                # ==========================================

                elif (
                    candidate_tokens
                    and candidate_tokens
                    .issubset(
                        requested_tokens
                    )
                ):

                    extras = (
                        len(requested_tokens)
                        - len(candidate_tokens)
                    )

                    score = (
                        88
                        - (
                            extras
                            * 12
                        )
                    )

                # ==========================================
                # 5. Fuzzy lexical similarity
                # ==========================================

                similarity = (
                    SequenceMatcher(
                        None,
                        requested,
                        candidate,
                    )
                    .ratio()
                )

                if (
                    similarity
                    >=
                    self
                    .FUZZY_SIMILARITY_THRESHOLD
                ):

                    fuzzy_score = int(
                        100
                        * similarity
                    )

                    score = max(
                        score,
                        fuzzy_score,
                    )

                # ==========================================
                # 6. Token overlap
                # ==========================================

                overlap = (
                    requested_tokens
                    & candidate_tokens
                )

                if overlap:

                    overlap_ratio = (
                        len(overlap)
                        /
                        max(
                            len(
                                requested_tokens
                            ),
                            1,
                        )
                    )

                    overlap_score = int(
                        overlap_ratio
                        * 55
                    )

                    score = max(
                        score,
                        overlap_score,
                    )

                variant_score = max(
                    variant_score,
                    score,
                )

            # ==============================================
            # Description evidence
            # ==============================================

            if (
                requested_tokens
                and requested_tokens
                .issubset(
                    description_tokens
                )
            ):

                variant_score += 18

            else:

                description_similarity = (
                    SequenceMatcher(
                        None,
                        requested,
                        description,
                    )
                    .ratio()
                    if description
                    else 0.0
                )

                if (
                    description_similarity
                    >= 0.75
                ):

                    variant_score += 10

            best_lexical_score = max(
                best_lexical_score,
                variant_score,
            )

        score = (
            best_lexical_score
        )

        # ==================================================
        # Metadata suitability
        # ==================================================

        if prefer_measure:

            if "measure" in category:
                score += 22

            if "financial" in category:
                score += 12

            if self._is_numeric_type(
                data_type
            ):
                score += 8

        else:

            if "dimension" in category:
                score += 8

            if "identifier" in category:
                score += 5

            if "flag" in category:
                score += 4

        # ==================================================
        # Generic semantic type hints
        # ==================================================

        combined_requested = (
            " ".join(
                sorted(
                    variants
                )
            )
        )

        if (
            "date" in combined_requested
            or "time" in combined_requested
        ):

            if self._is_date_type(
                data_type
            ):

                score += 12

        if (
            "amount" in combined_requested
            or "total" in combined_requested
            or "balance" in combined_requested
            or "rate" in combined_requested
            or "price" in combined_requested
        ):

            if self._is_numeric_type(
                data_type
            ):

                score += 10

        return max(
            score,
            0,
        )

    # ======================================================
    # Optional semantic aliases
    # ======================================================

    def _column_aliases(
        self,
        column: ColumnMetadata,
    ) -> set[str]:

        raw_aliases = []

        for field_name in (
            "aliases",
            "business_aliases",
        ):

            value = getattr(
                column,
                field_name,
                None,
            )

            if value is None:
                continue

            if isinstance(
                value,
                str,
            ):

                if "|" in value:

                    raw_aliases.extend(
                        value.split("|")
                    )

                else:

                    raw_aliases.append(
                        value
                    )

            elif isinstance(
                value,
                (
                    list,
                    tuple,
                    set,
                ),
            ):

                raw_aliases.extend(
                    str(item)
                    for item
                    in value
                )

        return {
            self._normalize(
                alias
            )

            for alias
            in raw_aliases

            if str(
                alias
            ).strip()
        }

    # ======================================================
    # Data-type helpers
    # ======================================================

    @classmethod
    def _is_numeric_type(
        cls,
        data_type: str,
    ) -> bool:

        return any(
            marker in data_type

            for marker
            in cls.NUMERIC_TYPE_MARKERS
        )

    @classmethod
    def _is_date_type(
        cls,
        data_type: str,
    ) -> bool:

        return any(
            marker in data_type

            for marker
            in cls.DATE_TYPE_MARKERS
        )

    # ======================================================
    # Normalization
    # ======================================================

    @staticmethod
    def _normalize(
        value: str,
    ) -> str:

        value = str(
            value
            or ""
        ).strip()

        # Acronym boundary.
        value = re.sub(
            r"([A-Z]+)([A-Z][a-z])",
            r"\1 \2",
            value,
        )

        # camelCase / PascalCase boundary.
        value = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1 \2",
            value,
        )

        value = value.lower()

        # snake_case / kebab-case.
        value = re.sub(
            r"[_\-]+",
            " ",
            value,
        )

        # Remove remaining punctuation.
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