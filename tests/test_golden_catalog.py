from golden.catalog import (
    GOLDEN_QUESTIONS,
)


def test_golden_catalog_not_empty():

    assert GOLDEN_QUESTIONS


def test_golden_question_ids_are_unique():

    ids = [
        golden.id
        for golden
        in GOLDEN_QUESTIONS
    ]

    assert len(ids) == len(
        set(ids)
    )


def test_golden_questions_have_text():

    for golden in GOLDEN_QUESTIONS:

        assert golden.question.strip()


def test_supported_questions_exist():

    supported = [
        golden
        for golden
        in GOLDEN_QUESTIONS
        if golden.capability
        == "SUPPORTED"
    ]

    assert supported


def test_golden_capability_matrix():

    counts = {
        "SUPPORTED": 0,
        "SAFE_REJECT": 0,
        "PLANNED": 0,
    }

    for golden in GOLDEN_QUESTIONS:

        counts[
            golden.capability
        ] += 1

    print()
    print("=" * 60)
    print("GOLDEN QUESTION CAPABILITY MATRIX")
    print("=" * 60)

    for capability, count in counts.items():

        print(
            f"{capability:<15}: "
            f"{count}"
        )

    print(
        f"{'TOTAL':<15}: "
        f"{len(GOLDEN_QUESTIONS)}"
    )

    print("=" * 60)

    assert sum(
        counts.values()
    ) == len(
        GOLDEN_QUESTIONS
    )