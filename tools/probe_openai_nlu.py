from ai.providers.openai_chat import OpenAIChatModel

from database.neo4j import Neo4jClient

from nlu.question_analyzer import QuestionAnalyzer

from ontology.concept_catalog import ConceptCatalogService


def main() -> None:

    print("=" * 70)
    print("OPENAI DYNAMIC NLU PROBE")
    print("=" * 70)

    neo4j = Neo4jClient()

    try:

        model = OpenAIChatModel()

        analyzer = QuestionAnalyzer(
            chat_model=model,
            concept_catalog=ConceptCatalogService(
                neo4j
            ),
        )

        questions = [
            "How much has Blue Prism paid?",
            "How many invoices does Blue Prism have?",
        ]

        for question in questions:

            print()
            print("QUESTION")
            print("-" * 70)
            print(question)

            result = analyzer.analyze(
                question
            )

            print()
            print("QUESTION ANALYSIS")
            print("-" * 70)

            print(
                result.model_dump()
            )

    finally:

        neo4j.close()


if __name__ == "__main__":
    main()