import re
from decimal import Decimal
from typing import Any

from answering.models import (
    AnswerResponse,
)

from nlu.models import (
    QuestionAnalysis,
)

from result_analysis.models import (
    ResultAnalysis,
)


class AnswerService:
    """
    Convert ResultAnalysis into an application-facing
    factual answer.

    This implementation is deterministic.

    It does NOT:
    - call an LLM
    - generate SQL
    - call Neo4j
    - call PostgreSQL
    - modify factual results
    - assume currency units
    - contain business-concept-specific mappings
    """

    def build(
        self,
        analysis: QuestionAnalysis,
        result: ResultAnalysis,
    ) -> AnswerResponse:

        # ==================================================
        # 1. Empty result
        # ==================================================

        if result.result_type == "empty":

            return AnswerResponse(
                answer=(
                    "No matching records were found."
                ),

                result_type="empty",

                row_count=0,

                data=None,

                warnings=result.warnings,
            )

        # ==================================================
        # 2. Scalar result
        # ==================================================

        if result.result_type == "scalar":

            return self._build_scalar_answer(
                analysis=analysis,
                result=result,
            )

        # ==================================================
        # 3. Tabular result
        # ==================================================

        if result.result_type == "tabular":

            return self._build_tabular_answer(
                result=result
            )

        raise ValueError(
            "Unsupported result type: "
            f"{result.result_type}"
        )

    # ======================================================
    # Scalar answer
    # ======================================================

    def _build_scalar_answer(
        self,
        analysis: QuestionAnalysis,
        result: ResultAnalysis,
    ) -> AnswerResponse:

        value = result.scalar_value

        # --------------------------------------------------
        # SQL aggregate functions such as SUM may return
        # NULL when no qualifying rows exist.
        #
        # NULL must never silently become zero.
        # --------------------------------------------------

        if value is None:

            return AnswerResponse(
                answer=(
                    "No aggregate value was available "
                    "for the requested criteria."
                ),

                result_type="scalar",

                row_count=result.row_count,

                data=None,

                warnings=result.warnings,
            )

        formatted_value = (
            self._format_value(
                value
            )
        )

        subject = (
            self._resolve_subject(
                analysis
            )
        )

        aggregation = (
            analysis.aggregation.upper()
            if analysis.aggregation
            else None
        )

        # ==================================================
        # COUNT
        #
        # IMPORTANT:
        #
        # For COUNT, measure_attribute may simply be the
        # physical counting key such as "id".
        #
        # The answer should describe the BUSINESS concept
        # being counted instead.
        #
        # Example:
        #
        # measure_concept   = Invoice
        # measure_attribute = id
        #
        # -> "count of invoice records"
        #
        # No concept-specific mapping is used.
        # ==================================================

        if aggregation == "COUNT":

            concept_label = (
                self._humanize(
                    analysis.measure_concept
                    or analysis.result_concept
                    or "record"
                )
            )

            if subject:

                answer = (
                    f"The count of "
                    f"{concept_label} records "
                    f"for {subject} is "
                    f"{formatted_value}."
                )

            else:

                answer = (
                    f"The count of "
                    f"{concept_label} records "
                    f"is {formatted_value}."
                )

        # ==================================================
        # SUM / AVG / MIN / MAX / scalar measure
        # ==================================================

        else:

            measure_label = (
                self._humanize(
                    analysis.measure_attribute
                    or result.scalar_alias
                    or "value"
                )
            )

            aggregation_label = (
                self._aggregation_label(
                    analysis.aggregation
                )
            )

            if subject:

                answer = (
                    f"The {aggregation_label}"
                    f"{measure_label} "
                    f"for {subject} is "
                    f"{formatted_value}."
                )

            else:

                answer = (
                    f"The {aggregation_label}"
                    f"{measure_label} is "
                    f"{formatted_value}."
                )

        return AnswerResponse(
            answer=answer,

            result_type="scalar",

            row_count=result.row_count,

            data={
                "alias":
                    result.scalar_alias,

                "value":
                    value,
            },

            warnings=result.warnings,
        )

    # ======================================================
    # Tabular answer
    # ======================================================

    @staticmethod
    def _build_tabular_answer(
        result: ResultAnalysis,
    ) -> AnswerResponse:

        if result.truncated:

            answer = (
                f"Returned the first "
                f"{result.row_count} matching rows. "
                "Additional rows were truncated."
            )

        elif result.row_count == 1:

            answer = (
                "Returned 1 matching row."
            )

        else:

            answer = (
                f"Returned "
                f"{result.row_count} "
                f"matching rows."
            )

        return AnswerResponse(
            answer=answer,

            result_type="tabular",

            row_count=result.row_count,

            data=result.rows,

            warnings=result.warnings,
        )

    # ======================================================
    # Subject resolution
    # ======================================================

    @staticmethod
    def _resolve_subject(
        analysis: QuestionAnalysis,
    ) -> str | None:
        """
        Use explicit entity values from the original
        business question as presentation context.

        No concept-specific rules are used.
        """

        if not analysis.entities:
            return None

        values = [
            entity.value
            for entity
            in analysis.entities
            if entity.value
        ]

        if not values:
            return None

        return ", ".join(
            values
        )

    # ======================================================
    # Aggregation wording
    # ======================================================

    @staticmethod
    def _aggregation_label(
        aggregation: str | None,
    ) -> str:
        """
        Generic SQL aggregation presentation semantics.
        """

        mapping = {
            "SUM": "total ",
            "AVG": "average ",
            "MIN": "minimum ",
            "MAX": "maximum ",
        }

        if aggregation is None:
            return ""

        return mapping.get(
            aggregation.upper(),
            "",
        )

    # ======================================================
    # Human-readable business label
    # ======================================================

    @staticmethod
    def _humanize(
        value: str,
    ) -> str:
        """
        Convert ontology/business identifiers into
        readable labels.

        Examples:

        PaymentReceipt
            -> payment receipt

        InvoiceClaim
            -> invoice claim

        GSTInvoiceInfo
            -> gst invoice info

        payment_amount
            -> payment amount
        """

        value = value.replace(
            "_",
            " ",
        )

        # ABCSomething -> ABC Something
        value = re.sub(
            r"(.)([A-Z][a-z]+)",
            r"\1 \2",
            value,
        )

        # paymentReceipt -> payment Receipt
        # Invoice2Item -> Invoice2 Item
        value = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1 \2",
            value,
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return (
            value
            .strip()
            .lower()
        )

    # ======================================================
    # Value formatting
    # ======================================================

    @staticmethod
    def _format_value(
        value: Any,
    ) -> str:

        if isinstance(
            value,
            Decimal,
        ):

            return format(
                value,
                "f",
            )

        if isinstance(
            value,
            float,
        ):

            return (
                f"{value:g}"
            )

        return str(
            value
        )