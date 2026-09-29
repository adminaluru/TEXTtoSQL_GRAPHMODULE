import re

from difflib import SequenceMatcher

from nlu.models import QuestionAnalysis

from ontology.models import (
    ColumnMetadata,
)

from ontology.ontology_service import (
    OntologyService,
)

from planning.column_models import (
    ColumnResolution,
    ResolvedFilterColumn,
    ResolvedMeasureColumn,
)

from planning.graph_models import GraphPlan


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
    ) -> None:

        self._ontology = (
            ontology_service
        )

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
                        "=",

                    value=
                        entity.value,

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

        return ColumnResolution(
            filters=
                resolved_filters,

            measure=
                resolved_measure,
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
        #
        # A winner is not trustworthy merely because it
        # beats #2 by one or two points.
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

    # ======================================================
    # Attribute variants
    # ======================================================

    def _attribute_variants(
        self,
        concept: str,
        attribute: str,
    ) -> set[str]:
        """
        Generate generic business-language variants.

        Example:

            concept:
                Customer

            attribute:
                customer name

        produces:

            customer name
            name

        No Customer-specific rule exists.
        """

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
        #
        # Customer + customer name
        #     -> name
        #
        # PaymentReceipt + payment receipt amount
        #     -> amount
        #
        # InvoiceClaim + invoice claim amount
        #     -> amount
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
        #
        # This helps when concept names contain multiple
        # words but business language uses only the final
        # concept word.
        #
        # InvoiceClaim + claim amount
        #     -> amount
        #
        # PaymentReceipt + receipt amount
        #     -> amount
        #
        # Only leading tokens are removed, never arbitrary
        # words from the middle of the phrase.
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

        for requested in variants:

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
                # 3. Requested phrase is contained in
                #    candidate column tokens.
                #
                # "name" vs "legal name"
                #
                # Extra candidate tokens are penalized.
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
                # 4. Candidate is contained in request.
                #
                # Useful for mild business-language noise.
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
                #
                # Handles physical spelling anomalies such
                # as:
                #
                # promise amount
                # promice amount
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

                best_lexical_score = max(
                    best_lexical_score,
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

                best_lexical_score += 18

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

                    best_lexical_score += 10

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

            # Filters commonly target dimensions,
            # identifiers, flags and dates rather than
            # monetary measures.

            if "dimension" in category:
                score += 8

            if "identifier" in category:
                score += 5

            if "flag" in category:
                score += 4

        # ==================================================
        # Generic semantic type hints derived from the
        # requested business phrase.
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
        """
        Support semantic aliases when they are later added
        to Neo4j/ColumnMetadata.

        This is backward-compatible with the current
        ColumnMetadata model.

        Supported future metadata properties:

            aliases
            business_aliases

        Example ontology metadata:

            physical:
                promice_amount

            aliases:
                promise amount
                promised amount
        """

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

                # Permit either:
                #
                # "promise amount"
                #
                # or:
                #
                # "promise amount|promised amount"
                #

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
        """
        Normalize business phrases and physical column
        identifiers into comparable lexical form.

        Examples:

            payment_amount
                -> payment amount

            Payment Amount
                -> payment amount

            payment-amount
                -> payment amount

            paymentAmount
                -> payment amount

            PaymentReceipt
                -> payment receipt

            GSTInvoiceInfo
                -> gst invoice info
        """

        value = str(
            value
            or ""
        ).strip()

        # --------------------------------------------------
        # Acronym boundary:
        #
        # GSTInvoice
        #     -> GST Invoice
        # --------------------------------------------------

        value = re.sub(
            r"([A-Z]+)([A-Z][a-z])",
            r"\1 \2",
            value,
        )

        # --------------------------------------------------
        # camelCase / PascalCase boundary:
        #
        # paymentAmount
        #     -> payment Amount
        # --------------------------------------------------

        value = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1 \2",
            value,
        )

        value = value.lower()

        # snake_case / kebab-case

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