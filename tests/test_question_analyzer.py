from ai.providers.mock_chat import MockChatModel
from nlu.question_analyzer import QuestionAnalyzer


def test_customer_payment_question():

    model = MockChatModel(
        structured_response={
            "intent": "aggregate",

            "concepts": [
                "Customer",
                "PaymentReceipt",
            ],

            "result_concept":
                "Customer",

            "measure_concept":
                "PaymentReceipt",

            "measure_attribute":
                "payment amount",

            "group_by_concepts": [],

            "entities": [
                {
                    "concept":
                        "Customer",

                    "attribute":
                        "name",

                    "value":
                        "Blue Prism",
                }
            ],

            "aggregation":
                "SUM",
        }
    )

    analyzer = QuestionAnalyzer(
        model
    )

    result = analyzer.analyze(
        "How much has Blue Prism paid?"
    )

    assert result.intent == "aggregate"

    assert result.concepts == [
        "Customer",
        "PaymentReceipt",
    ]

    assert (
        result.result_concept
        == "Customer"
    )

    assert (
        result.measure_concept
        == "PaymentReceipt"
    )

    assert (
        result.measure_attribute
        == "payment amount"
    )

    assert (
        result.group_by_concepts
        == []
    )

    assert len(
        result.entities
    ) == 1

    entity = result.entities[0]

    assert (
        entity.concept
        == "Customer"
    )

    assert (
        entity.attribute
        == "name"
    )

    assert (
        entity.value
        == "Blue Prism"
    )

    assert (
        result.aggregation
        == "SUM"
    )