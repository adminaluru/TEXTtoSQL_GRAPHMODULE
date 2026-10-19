import logging

from pydantic import BaseModel

from ai.interfaces.chat_model import (
    ChatMessage,
    ChatModel,
    ChatRequest,
)

from nlu.models import (
    QuestionAnalysis,
)

from observability.perf import (
    PerfTrace,
)

from ontology.concept_catalog import (
    BusinessConceptItem,
    ConceptCatalogService,
)


logger = logging.getLogger(
    "uvicorn.error"
)


class QuestionAnalyzer:
    """
    Convert a natural-language business question into
    structured QuestionAnalysis.

    LLM responsibility:

        natural language
            ->
        business intent

    The LLM must NOT:

    - generate SQL
    - choose physical PostgreSQL tables
    - choose physical PostgreSQL columns
    - invent joins
    - invent ontology concepts

    Available business concepts are supplied dynamically
    from Neo4j through ConceptCatalogService.

    Performance instrumentation measures:

    - ontology catalog loading
    - prompt construction
    - ChatRequest construction
    - structured LLM call
    - result coercion
    - concept validation/canonicalization
    - total QuestionAnalyzer latency

    Timing instrumentation does NOT change business logic.
    """

    PERF_STAGES = [
        "catalog",
        "prompt_build",
        "request_build",
        "llm_structured_call",
        "result_coercion",
        "concept_validation",
    ]

    def __init__(
        self,
        chat_model: ChatModel,
        concept_catalog: ConceptCatalogService | None = None,
    ) -> None:

        self._chat_model = chat_model

        self._concept_catalog = (
            concept_catalog
        )

    # ======================================================
    # Public API
    # ======================================================

    def analyze(
        self,
        question: str,
        repair_messages: list[ChatMessage] | None = None,
    ) -> QuestionAnalysis:

        question = (
            question.strip()
        )

        if not question:

            raise ValueError(
                "Question cannot be empty."
            )

        with PerfTrace(
            event="FLOWONE_NLU_PERF",
            id_field="analyzer_id",
            stage_order=self.PERF_STAGES,
            logger=logger,
            fields={
                "catalog_size": None,
            },
        ) as trace:

            # ==================================================
            # 1. Load current ontology vocabulary
            #
            # Neo4j-backed.
            # ==================================================

            with trace.stage("catalog"):

                catalog = (
                    self._load_catalog()
                )

            trace.fields[
                "catalog_size"
            ] = len(
                catalog
            )

            # ==================================================
            # 2. Build dynamic system prompt
            # ==================================================

            with trace.stage("prompt_build"):

                system_prompt = (
                    self._build_system_prompt(
                        catalog
                    )
                )

            # ==================================================
            # 3. Build provider-neutral ChatRequest
            # ==================================================

            with trace.stage("request_build"):

                messages = [
                    ChatMessage(
                        role="system",
                        content=
                            system_prompt,
                    ),
                    ChatMessage(
                        role="user",
                        content=
                            question,
                    ),
                ]

                # ----------------------------------------------
                # Repair attempts append the previous structured
                # answer and the deterministic rejection so the
                # model can correct itself.
                # ----------------------------------------------

                messages.extend(
                    repair_messages
                    or []
                )

                request = (
                    ChatRequest(
                        messages=
                            messages,

                        temperature=0.0,
                    )
                )

            # ==================================================
            # 4. LLM -> structured QuestionAnalysis
            #
            # Includes network request, provider inference and
            # SDK structured-output processing.
            # ==================================================

            with trace.stage("llm_structured_call"):

                raw_result = (
                    self._chat_model
                    .generate_structured(
                        request=
                            request,

                        response_schema=
                            QuestionAnalysis,
                    )
                )

            # ==================================================
            # 5. Structured-result coercion
            # ==================================================

            with trace.stage("result_coercion"):

                analysis = (
                    self._coerce_analysis(
                        raw_result
                    )
                )

            # ==================================================
            # 6. Deterministically validate/canonicalize
            #    concepts against live Neo4j vocabulary.
            #
            # Prompt instructions alone are NOT trusted.
            # ==================================================

            with trace.stage("concept_validation"):

                if catalog:

                    analysis = (
                        self._validate_and_canonicalize(
                            analysis=
                                analysis,

                            catalog=
                                catalog,
                        )
                    )

            return analysis

    # ======================================================
    # Ontology vocabulary
    # ======================================================

    def _load_catalog(
        self,
    ) -> list[
        BusinessConceptItem
    ]:
        """
        Load vocabulary when an ontology catalog service
        is configured.

        Tests using MockChatModel may intentionally omit
        the catalog service.

        Runtime ResearchAgentFactory supplies it.
        """

        if (
            self._concept_catalog
            is None
        ):

            return []

        catalog = (
            self._concept_catalog
            .list_business_concepts()
        )

        if not catalog:

            raise RuntimeError(
                "Neo4j ontology returned an empty "
                "business concept catalog."
            )

        return catalog

    # ======================================================
    # Dynamic Prompt
    # ======================================================

    def _build_system_prompt(
        self,
        catalog: list[
            BusinessConceptItem
        ],
    ) -> str:
        """
        Build the NLU prompt from the live ontology.

        No FlowOne table/concept examples are hard-coded.
        """

        catalog_text = (
            self._format_catalog(
                catalog
            )
        )

        return f"""
You are the Question Analyzer for a business Research Agent.

Your responsibility is to understand the BUSINESS meaning
of the user's question and return structured data matching
the required QuestionAnalysis schema.

You are NOT a SQL generator.

Do NOT:
- generate SQL
- return PostgreSQL table names
- return PostgreSQL column names
- invent joins
- invent database schema
- invent business concepts

============================================================
AVAILABLE BUSINESS CONCEPTS
============================================================

{catalog_text}

============================================================
CONCEPT RULES
============================================================

When an ontology catalog is provided:

1. Every value in "concepts" must be selected from the
   AVAILABLE BUSINESS CONCEPTS above.

2. "result_concept" must be one of those concepts or null.

3. "measure_concept" must be one of those concepts or null.

4. Every entity "concept" must be one of those concepts.

5. Every "group_by_concepts" value must be one of those
   concepts.

6. Preserve business meaning.
   Do not convert concepts into physical table names.

============================================================
OUTPUT FIELDS
============================================================

intent

Allowed values:
- aggregate
- count
- list
- lookup
- unknown


concepts

Business concepts required to answer the question.


result_concept

The business concept representing the requested result
or grouping level.


measure_concept

The business concept containing the value being measured
or aggregated.


measure_attribute

Business-language name of the value being measured.

Use business terminology only.

Do NOT return physical PostgreSQL column names.


group_by_concepts

Business concepts requested as grouping dimensions.


entities

Business filters extracted from the question.

Each entity must contain:

- concept
- attribute
- value

The attribute must remain business-level terminology.

Never omit a condition from the question. Include every
condition (names, dates or periods, statuses, amounts,
comparisons) as an entity, using the business attribute
it applies to, even if it may not be supported. Leaving a
condition out would answer a different question.


aggregation

Allowed values:
- SUM
- COUNT
- AVG
- MIN
- MAX
- null

Choose the aggregation only when the user clearly asks
for an aggregate operation.

Return only the structured QuestionAnalysis response.
""".strip()

    # ======================================================
    # Catalog formatting
    # ======================================================

    @staticmethod
    def _format_catalog(
        catalog: list[
            BusinessConceptItem
        ],
    ) -> str:

        if not catalog:

            return (
                "No ontology catalog was supplied "
                "for this execution."
            )

        lines = []

        for item in catalog:

            parts = [
                item.concept
            ]

            if item.domain:

                parts.append(
                    f"domain={item.domain}"
                )

            if item.description:

                description = (
                    " ".join(
                        item.description.split()
                    )
                )

                parts.append(
                    f"description={description}"
                )

            lines.append(
                "- "
                + " | ".join(
                    parts
                )
            )

        return "\n".join(
            lines
        )

    # ======================================================
    # Structured result coercion
    # ======================================================

    @staticmethod
    def _coerce_analysis(
        value: BaseModel,
    ) -> QuestionAnalysis:

        if isinstance(
            value,
            QuestionAnalysis,
        ):

            return value

        if isinstance(
            value,
            BaseModel,
        ):

            return (
                QuestionAnalysis
                .model_validate(
                    value.model_dump()
                )
            )

        return (
            QuestionAnalysis
            .model_validate(
                value
            )
        )

    # ======================================================
    # Deterministic ontology enforcement
    # ======================================================

    def _validate_and_canonicalize(
        self,
        analysis: QuestionAnalysis,
        catalog: list[
            BusinessConceptItem
        ],
    ) -> QuestionAnalysis:
        """
        Do not trust the LLM merely because the prompt
        told it not to invent concepts.

        Every returned concept is checked against the
        actual Neo4j vocabulary.

        Matching is case-insensitive, but canonical Neo4j
        spelling is restored.
        """

        lookup = {
            item.concept.lower():
                item.concept

            for item
            in catalog
        }

        canonical_concepts = [
            self._canonicalize_concept(
                concept,
                lookup,
            )

            for concept
            in analysis.concepts
        ]

        result_concept = None

        if analysis.result_concept:

            result_concept = (
                self._canonicalize_concept(
                    analysis.result_concept,
                    lookup,
                )
            )

        measure_concept = None

        if analysis.measure_concept:

            measure_concept = (
                self._canonicalize_concept(
                    analysis.measure_concept,
                    lookup,
                )
            )

        canonical_groups = [
            self._canonicalize_concept(
                concept,
                lookup,
            )

            for concept
            in analysis.group_by_concepts
        ]

        canonical_entities = [
            entity.model_copy(
                update={
                    "concept":
                        self._canonicalize_concept(
                            entity.concept,
                            lookup,
                        )
                }
            )

            for entity
            in analysis.entities
        ]

        return (
            analysis.model_copy(
                update={
                    "concepts":
                        canonical_concepts,

                    "result_concept":
                        result_concept,

                    "measure_concept":
                        measure_concept,

                    "group_by_concepts":
                        canonical_groups,

                    "entities":
                        canonical_entities,
                }
            )
        )

    # ======================================================
    # Concept canonicalization
    # ======================================================

    @staticmethod
    def _canonicalize_concept(
        concept: str,
        lookup: dict[
            str,
            str,
        ],
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
                "LLM returned a business concept "
                "that does not exist in the "
                f"Neo4j ontology: {concept}"
            )

        return canonical