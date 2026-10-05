from collections import deque
from dataclasses import dataclass

from ontology.ontology_service import (
    OntologyService,
)

from planning.models import (
    QueryPlan,
)


@dataclass(frozen=True)
class FanOutViolation:
    """
    One join that would repeat rows of the measure table.
    """

    function: str

    measure_table: str

    from_table: str
    from_column: str

    to_table: str
    to_column: str

    @property
    def message(self) -> str:

        return (
            f"{self.function} over {self.measure_table} would "
            "be multiplied by the one-to-many join "
            f"{self.from_table}.{self.from_column} -> "
            f"{self.to_table}.{self.to_column}"
        )


class FanOutGuard:
    """
    Block aggregations whose measure rows would be
    multiplied by a one-to-many join.

    Example of the defect this prevents:

        SUM(invoice.amount)
        FROM invoice
        JOIN payment ON payment.invoice_id = invoice.id

    An invoice with three payments contributes its amount
    three times, so the total is silently inflated.

    Rule:

    Starting at the aggregated (measure) table, walk the
    outer-query joins. Each newly joined table must be
    reached through its own single-column primary key, so
    every measure row matches at most one row there.
    Any other join can fan out and is blocked.

    Uniqueness comes from Column.is_primary_key in the
    Neo4j ontology; no tables are hard-coded. Unknown
    metadata fails closed.

    Scope:

    - SUM, AVG and COUNT are guarded (duplicates change
      the result); MIN and MAX are not
    - existence conditions compile to EXISTS, which never
      multiplies outer rows, so they are not walked
    """

    GUARDED_AGGREGATIONS = {
        "SUM",
        "AVG",
        "COUNT",
    }

    def __init__(
        self,
        ontology_service: OntologyService,
    ) -> None:

        self._ontology = ontology_service

    # ======================================================
    # Public API
    # ======================================================

    def check(
        self,
        plan: QueryPlan,
    ) -> list[str]:
        """
        Return violation messages; an empty list means the
        plan cannot double-count.
        """

        return [
            violation.message

            for violation
            in self.find(
                plan
            )
        ]

    def find(
        self,
        plan: QueryPlan,
    ) -> list[FanOutViolation]:
        """
        Return structured violations, one per offending
        join and guarded aggregation.
        """

        guarded = [
            aggregation

            for aggregation
            in plan.aggregations

            if aggregation.function.upper()
            in self.GUARDED_AGGREGATIONS
        ]

        if not guarded or not plan.joins:
            return []

        violations: list[FanOutViolation] = []

        for aggregation in guarded:

            violations.extend(
                self._walk_from(
                    root_table=
                        aggregation.table,

                    function=
                        aggregation.function.upper(),

                    plan=
                        plan,
                )
            )

        return list(
            dict.fromkeys(
                violations
            )
        )

    # ======================================================
    # Join walk
    # ======================================================

    def _walk_from(
        self,
        root_table: str,
        function: str,
        plan: QueryPlan,
    ) -> list[FanOutViolation]:

        # Undirected adjacency: table -> [(its column,
        # neighbour table, neighbour column)]
        adjacency: dict[
            str,
            list[tuple[str, str, str]],
        ] = {}

        for join in plan.joins:

            adjacency.setdefault(
                join.left_table, []
            ).append(
                (
                    join.left_column,
                    join.right_table,
                    join.right_column,
                )
            )

            adjacency.setdefault(
                join.right_table, []
            ).append(
                (
                    join.right_column,
                    join.left_table,
                    join.left_column,
                )
            )

        violations: list[FanOutViolation] = []

        visited = {root_table}

        queue = deque([root_table])

        while queue:

            table = queue.popleft()

            for (
                column,
                neighbour,
                neighbour_column,
            ) in adjacency.get(table, []):

                if neighbour in visited:
                    continue

                visited.add(neighbour)

                queue.append(neighbour)

                if not self._is_unique(
                    neighbour,
                    neighbour_column,
                ):

                    violations.append(
                        FanOutViolation(
                            function=function,
                            measure_table=root_table,
                            from_table=table,
                            from_column=column,
                            to_table=neighbour,
                            to_column=neighbour_column,
                        )
                    )

        return violations

    # ======================================================
    # Uniqueness
    # ======================================================

    def _is_unique(
        self,
        table: str,
        column: str,
    ) -> bool:
        """
        True when column is the table's single-column
        primary key.
        """

        primary_keys = [
            metadata.name

            for metadata
            in self._ontology.get_table_columns(
                table
            )

            if metadata.is_primary_key
        ]

        return primary_keys == [column]
