from ontology.models import (
    JoinMetadata,
    PhysicalPath,
)

from planning.path_ranker import (
    PathRanker,
)


def test_path_ranker_prefers_confirmed_executable_path():
    """
    Execution safety must have higher priority
    than shortest-path scoring.

    Unsafe-but-short:

        A -> B

        validation_status =
            composite_join_recommended

    Safe-but-longer:

        A -> Bridge -> B

        both joins confirmed

    SQLGuardrails currently executes only
    confirmed joins.

    Therefore PathRanker.select_best() must
    choose the confirmed path.
    """

    warning_path = PhysicalPath(
        tables=[
            "public.table_a",
            "public.table_b",
        ],

        joins=[
            JoinMetadata(
                source_table=
                    "public.table_a",

                target_table=
                    "public.table_b",

                source_column=
                    "id",

                target_column=
                    "a_id",

                join_condition=(
                    "public.table_a.id = "
                    "public.table_b.a_id"
                ),

                validation_status=
                    "composite_join_recommended",

                cardinality=
                    "one_to_many",
            )
        ],
    )

    confirmed_path = PhysicalPath(
        tables=[
            "public.table_a",
            "public.bridge_table",
            "public.table_b",
        ],

        joins=[
            JoinMetadata(
                source_table=
                    "public.table_a",

                target_table=
                    "public.bridge_table",

                source_column=
                    "id",

                target_column=
                    "a_id",

                join_condition=(
                    "public.table_a.id = "
                    "public.bridge_table.a_id"
                ),

                validation_status=
                    "confirmed",

                cardinality=
                    "one_to_many",
            ),

            JoinMetadata(
                source_table=
                    "public.bridge_table",

                target_table=
                    "public.table_b",

                source_column=
                    "b_id",

                target_column=
                    "id",

                join_condition=(
                    "public.bridge_table.b_id = "
                    "public.table_b.id"
                ),

                validation_status=
                    "confirmed",

                cardinality=
                    "many_to_one",
            ),
        ],
    )

    selected = (
        PathRanker()
        .select_best(
            [
                warning_path,
                confirmed_path,
            ]
        )
    )

    assert (
        selected
        == confirmed_path
    )


def test_path_ranker_returns_none_when_no_executable_path():
    """
    If every candidate contains a non-confirmed
    physical join, planning should fail closed
    instead of selecting a path that Guardrails
    will inevitably reject later.
    """

    inferred_path = PhysicalPath(
        tables=[
            "public.table_a",
            "public.table_b",
        ],

        joins=[
            JoinMetadata(
                source_table=
                    "public.table_a",

                target_table=
                    "public.table_b",

                source_column=
                    "id",

                target_column=
                    "a_id",

                join_condition=(
                    "public.table_a.id = "
                    "public.table_b.a_id"
                ),

                validation_status=
                    "inferred",

                cardinality=
                    "one_to_many",
            )
        ],
    )

    selected = (
        PathRanker()
        .select_best(
            [
                inferred_path
            ]
        )
    )

    assert (
        selected
        is None
    )