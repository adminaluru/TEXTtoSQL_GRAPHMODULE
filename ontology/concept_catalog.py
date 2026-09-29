from pydantic import (
    BaseModel,
    ConfigDict,
)

from database.neo4j import (
    Neo4jClient,
)


class BusinessConceptItem(BaseModel):
    """
    One business concept exposed to the NLU layer.

    This intentionally contains business metadata only.

    Physical PostgreSQL table names are NOT exposed
    to the LLM.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    concept: str

    domain: str | None = None

    description: str | None = None


class ConceptCatalogService:
    """
    Read the current business vocabulary dynamically
    from the Neo4j ontology.

    Neo4j remains the authoritative source for which
    concepts the Research Agent understands.

    No business concepts are hard-coded here.
    """

    def __init__(
        self,
        neo4j_client: Neo4jClient,
    ) -> None:

        self._neo4j = neo4j_client

    def list_business_concepts(
        self,
    ) -> list[BusinessConceptItem]:
        """
        Return all available business concepts.

        Because business_concept uniqueness has already
        been validated, every concept should represent
        exactly one ontology Table node.
        """

        query = """
        MATCH (t:Table)

        WHERE
            t.business_concept IS NOT NULL
            AND trim(t.business_concept) <> ''

        RETURN
            t.business_concept AS concept,
            t.business_domain AS domain,
            t.description AS description

        ORDER BY
            toLower(t.business_concept)
        """

        rows = self._neo4j.execute_read(
            query,
            {},
        )

        return [
            BusinessConceptItem(
                concept=row["concept"],
                domain=row.get("domain"),
                description=row.get(
                    "description"
                ),
            )
            for row in rows
        ]