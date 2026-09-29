from decimal import Decimal

from answering.answer_service import (
    AnswerService,
)

from nlu.models import (
    QuestionAnalysis,
)

from result_analysis.models import (
    ResultAnalysis,
)


def test_scalar_customer_payment_answer():

    analysis = QuestionAnalysis(
        intent="aggregate",

        concepts=[
            "Customer",
            "PaymentReceipt",
        ],

        result_concept=
            "Customer",

        measure_concept=
            "PaymentReceipt",

        measure_attribute=
            "payment amount",

        group_by_concepts=[],

        entities=[
            {
                "concept":
                    "Customer",

                "attribute":
                    "name",

                "value":
                    "Blue Prism",
            }
        ],

        aggregation="SUM",
    )

    result = ResultAnalysis(
        result_type="scalar",

        columns=[
            "sum_payment_amount"
        ],

        row_count=1,

        truncated=False,

        scalar_alias=
            "sum_payment_amount",

        scalar_value=
            Decimal("12500.00"),

        rows=[
            {
                "sum_payment_amount":
                    Decimal("12500.00")
            }
        ],

        warnings=[],
    )

    response = (
        AnswerService()
        .build(
            analysis=analysis,
            result=result,
        )
    )

    assert (
        response.answer
        ==
        "The total payment amount "
        "for Blue Prism is 12500.00."
    )

    assert (
        response.result_type
        == "scalar"
    )

    assert (
        response.data["value"]
        == Decimal("12500.00")
    )


def test_scalar_null_is_not_zero():

    analysis = QuestionAnalysis(
        intent="aggregate",

        concepts=[
            "Customer",
            "PaymentReceipt",
        ],

        result_concept=
            "Customer",

        measure_concept=
            "PaymentReceipt",

        measure_attribute=
            "payment amount",

        group_by_concepts=[],

        entities=[],

        aggregation="SUM",
    )

    result = ResultAnalysis(
        result_type="scalar",

        columns=[
            "sum_payment_amount"
        ],

        row_count=1,

        truncated=False,

        scalar_alias=
            "sum_payment_amount",

        scalar_value=None,

        rows=[
            {
                "sum_payment_amount": None
            }
        ],

        warnings=[],
    )

    response = (
        AnswerService()
        .build(
            analysis=analysis,
            result=result,
        )
    )

    assert (
        response.answer
        ==
        "No aggregate value was available "
        "for the requested criteria."
    )

    assert response.data is None

    assert "0" not in response.answer


def test_empty_answer():

    analysis = QuestionAnalysis(
        intent="list",

        concepts=[
            "Customer"
        ],

        result_concept=
            "Customer",

        measure_concept=None,

        measure_attribute=None,

        group_by_concepts=[],

        entities=[],

        aggregation=None,
    )

    result = ResultAnalysis(
        result_type="empty",

        columns=[
            "id",
            "name",
        ],

        row_count=0,

        truncated=False,

        scalar_alias=None,

        scalar_value=None,

        rows=[],

        warnings=[],
    )

    response = (
        AnswerService()
        .build(
            analysis=analysis,
            result=result,
        )
    )

    assert (
        response.answer
        ==
        "No matching records were found."
    )

    assert response.row_count == 0


def test_tabular_answer():

    analysis = QuestionAnalysis(
        intent="list",

        concepts=[
            "Customer"
        ],

        result_concept=
            "Customer",

        measure_concept=None,

        measure_attribute=None,

        group_by_concepts=[],

        entities=[],

        aggregation=None,
    )

    result = ResultAnalysis(
        result_type="tabular",

        columns=[
            "id",
            "name",
        ],

        row_count=2,

        truncated=False,

        scalar_alias=None,

        scalar_value=None,

        rows=[
            {
                "id": 1,
                "name": "Customer A",
            },
            {
                "id": 2,
                "name": "Customer B",
            },
        ],

        warnings=[],
    )

    response = (
        AnswerService()
        .build(
            analysis=analysis,
            result=result,
        )
    )

    assert (
        response.answer
        ==
        "Returned 2 matching rows."
    )

    assert response.row_count == 2

    assert len(response.data) == 2