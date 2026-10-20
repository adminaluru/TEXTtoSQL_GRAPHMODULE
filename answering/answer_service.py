import re
from decimal import Decimal
from typing import Any, Sequence

from answering.models import (
    AnswerResponse,
)

from nlu.models import (
    QuestionAnalysis,
)

from semantic.existence import existence_concepts

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
        chosen_labels: Sequence[str] = (),
    ) -> AnswerResponse:
        """
        chosen_labels: labels of records the caller picked from a
        clarification; they name the subject instead of the ambiguous
        name typed in the question.
        """

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
                chosen_labels=chosen_labels,
            )

        # ==================================================
        # 3. Tabular result
        # ==================================================

        if result.result_type == "tabular":

            # One monetary value per currency (D-004).
            if (
                result.currency_alias
                and len(self._value_columns(result)) == 2
            ):
                return self._build_currency_split_answer(
                    analysis=analysis,
                    result=result,
                    chosen_labels=chosen_labels,
                )

            answer = self._build_tabular_answer(
                result=result
            )

            # Each row carries its currency, but a reader may still add
            # rows up: say when they are not in one currency (D-004).
            if result.currency_alias:
                answer.answer += self._mixed_currency_sentence(
                    [r.get(result.currency_alias) for r in result.rows]
                )

            if result.missing_count:
                answer.warnings.append(
                    self._missing_sentence(analysis, result.missing_count).strip()
                )

            return answer

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
        chosen_labels: Sequence[str] = (),
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

        # Monetary values always carry their currency (D-004).
        if result.currency_alias:
            formatted_value = (
                f"{formatted_value} "
                f"{self._currency_label(result.scalar_currency)}"
            )

        subject = (
            self._resolve_subject(
                analysis,
                chosen_labels,
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

            qualifier = (
                self._qualifier(analysis)
                + self._conditions(analysis)
            )

            if subject:

                answer = (
                    f"The count of "
                    f"{concept_label} records{qualifier} "
                    f"for {subject} is "
                    f"{formatted_value}."
                )

            else:

                answer = (
                    f"The count of "
                    f"{concept_label} records{qualifier} "
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

            scope = (
                self._record_scope(analysis)
                + self._conditions(analysis)
            )

            if subject:

                answer = (
                    f"The {aggregation_label}"
                    f"{measure_label}{scope} "
                    f"for {subject} is "
                    f"{formatted_value}."
                )

            else:

                answer = (
                    f"The {aggregation_label}"
                    f"{measure_label}{scope} is "
                    f"{formatted_value}."
                )

        answer += self._missing_sentence(
            analysis,
            result.missing_count,
        )

        response = AnswerResponse(
            answer=answer,

            result_type="scalar",

            row_count=result.row_count,

            data={
                "alias":
                    result.scalar_alias,
                "value":
                    value,
                **(
                    {"currency": result.scalar_currency}
                    if result.currency_alias
                    else {}
                ),
                **(
                    {"missing_count": result.missing_count}
                    if result.missing_alias
                    else {}
                ),
            },
            warnings=result.warnings,
        )

        response._figures = (formatted_value,)

        return response

    # ======================================================
    # Currency-split answer
    # ======================================================

    def _build_currency_split_answer(
        self,
        analysis: QuestionAnalysis,
        result: ResultAnalysis,
        chosen_labels: Sequence[str] = (),
    ) -> AnswerResponse:

        value_alias = next(
            c for c in self._value_columns(result)
            if c != result.currency_alias
        )

        rows = sorted(
            result.rows,
            key=lambda r: (
                r[result.currency_alias] is None,
                r[result.currency_alias] or "",
            ),
        )

        part_texts = [
            (
                self._format_value(r[value_alias])
                if r[value_alias] is not None
                else "no value"
            )
            + " "
            + self._currency_label(r[result.currency_alias])
            for r in rows
        ]

        parts = "; ".join(part_texts)

        measure_label = self._humanize(
            analysis.measure_attribute
            or value_alias
        )

        aggregation_label = self._aggregation_label(
            analysis.aggregation
        )

        subject = self._resolve_subject(analysis, chosen_labels)

        scope = f" for {subject}" if subject else ""

        response = AnswerResponse(
            answer=(
                f"The {aggregation_label}{measure_label}"
                f"{self._record_scope(analysis)}"
                f"{self._conditions(analysis)}{scope} "
                f"is reported per currency, because amounts in "
                f"different currencies are not added together: "
                f"{parts}."
                + self._missing_sentence(analysis, result.missing_count)
            ),
            result_type="tabular",
            row_count=result.row_count,
            data=rows,
            warnings=result.warnings,
        )

        response._figures = tuple(
            p for p, r in zip(part_texts, rows) if r[value_alias] is not None
        )

        return response

    @staticmethod
    def _value_columns(
        result: ResultAnalysis,
    ) -> list[str]:

        return [
            c for c in result.columns
            if c != result.missing_alias
        ]

    def _missing_sentence(
        self,
        analysis: QuestionAnalysis,
        missing: int | None,
    ) -> str:
        """
        D-006: records whose value is missing are excluded by SQL
        aggregation; say so instead of hiding the gap.
        """

        if not missing:
            return ""

        concept = self._humanize(
            analysis.measure_concept or "record"
        )

        measure = self._humanize(
            analysis.measure_attribute or "value"
        )

        plural = missing != 1

        return (
            f" {missing} {concept} record{'s' if plural else ''} "
            f"with no {measure} {'are' if plural else 'is'} "
            f"not included."
        )

    def _qualifier(
        self,
        analysis: QuestionAnalysis,
    ) -> str:
        """
        Conditions applied as EXISTS (semantic.existence), e.g.
        " with related <concept> records". An answer must
        never read as if it covered all records.
        """

        concepts = existence_concepts(
            intent=analysis.intent,
            concepts=analysis.concepts,
            result_concept=analysis.result_concept,
            measure_concept=analysis.measure_concept,
            filter_concepts={e.concept for e in analysis.entities},
            grouping_concepts=set(analysis.group_by_concepts),
        )

        if not concepts:
            return ""

        labels = [self._humanize(c) for c in concepts]

        joined = (
            labels[0]
            if len(labels) == 1
            else ", ".join(labels[:-1]) + " and " + labels[-1]
        )

        return f" with related {joined} records"

    def _record_scope(
        self,
        analysis: QuestionAnalysis,
    ) -> str:

        qualifier = self._qualifier(analysis)

        if not qualifier:
            return ""

        return (
            f" of {self._humanize(analysis.result_concept or 'record')} "
            f"records{qualifier}"
        )

    @staticmethod
    def _mixed_currency_sentence(
        currencies: Sequence[Any],
    ) -> str:

        known = sorted({str(c) for c in currencies if c not in (None, "")})
        unknown = any(c in (None, "") for c in currencies)

        if len(known) > 1:
            sentence = (
                f" Amounts are in {len(known)} currencies "
                f"({', '.join(known)}); do not add rows in "
                "different currencies together."
            )
        elif unknown and known:
            sentence = (
                " Some rows have no currency; do not add them to "
                f"the {known[0]} amounts."
            )
        else:
            return ""

        if unknown and len(known) > 1:
            sentence += " Some rows have no currency."

        return sentence

    @staticmethod
    def _currency_label(
        currency: Any,
    ) -> str:

        return (
            str(currency)
            if currency not in (None, "")
            else "(currency unknown)"
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
        chosen_labels: Sequence[str] = (),
    ) -> str | None:
        """
        Use explicit entity values from the original
        business question as presentation context.

        A record the caller picked from a clarification is named
        by its choice label; a typed name it covers is dropped so
        it is not repeated. No concept-specific rules are used.
        """

        if not analysis.entities and not chosen_labels:
            return None

        # Only named records are the subject ("for Blue Prism ...");
        # other conditions read "with invoice status OPEN".
        values = [
            entity.value
            for entity
            in analysis.entities
            if entity.value
            and AnswerService._is_name_attribute(entity.attribute)
        ]

        chosen = [label for label in chosen_labels if label]
        covered = [label.casefold() for label in chosen]
        values = chosen + [
            value
            for value in values
            if not any(value.casefold() in label for label in covered)
        ]

        if not values:
            return None

        return ", ".join(
            values
        )

    @staticmethod
    def _is_name_attribute(
        attribute: str,
    ) -> bool:

        normalized = " ".join(
            attribute.lower().split()
        )

        return (
            normalized == "name"
            or normalized.endswith(" name")
        )

    def _conditions(
        self,
        analysis: QuestionAnalysis,
    ) -> str:

        conditions = [
            f"{self._humanize(entity.attribute)} {entity.value}"
            for entity in analysis.entities
            if entity.value
            and not self._is_name_attribute(entity.attribute)
        ]

        if not conditions:
            return ""

        return " with " + " and ".join(conditions)

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