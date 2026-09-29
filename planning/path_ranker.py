from ontology.models import PhysicalPath

from planning.path_models import (
    RankedPhysicalPath,
)


class PathRanker:
    """
    Ranks candidate PostgreSQL join paths.

    Rule priority:

    1. Execution eligibility / join trust
    2. Then deterministic path ranking

    This prevents the planner from selecting a short path
    that SQLGuardrails will inevitably reject later.

    Lower score means a safer / better path among paths
    that are eligible for execution.

    Ranking currently considers:

    1. Number of joins
    2. Relationship validation status

    Later this can also consider:
    - query grain
    - cardinality
    - required concepts
    - business-domain alignment
    - composite join completeness

    Important:

    rank() intentionally still ranks all candidate paths for
    diagnostics/observability.

    select_best() filters to executable paths first and only
    then applies ranking.
    """

    # ======================================================
    # Execution eligibility
    #
    # Keep this aligned with SQLGuardrails.SAFE_JOIN_STATUSES.
    #
    # We intentionally do NOT import SQLGuardrails here,
    # because planning should not depend on the execution
    # guardrail implementation.
    # ======================================================

    EXECUTABLE_JOIN_STATUSES = {
        "confirmed",
    }

    # ======================================================
    # Ranking penalties
    # ======================================================

    STATUS_PENALTIES = {
        "confirmed": 0,

        "composite_join_recommended": 5,

        "confirmed_key_needs_validation": 15,

        "logical_grouping_key_not_unique": 25,

        "inferred": 40,
    }

    UNKNOWN_STATUS_PENALTY = 30

    HOP_PENALTY = 10

    # ======================================================
    # Public API
    # ======================================================

    def rank(
        self,
        paths: list[PhysicalPath],
    ) -> list[RankedPhysicalPath]:
        """
        Rank all candidate paths.

        This method does not remove unsafe candidates so
        diagnostics can still inspect how every discovered
        path scored.
        """

        ranked = [
            self._score_path(path)
            for path in paths
        ]

        return sorted(
            ranked,
            key=lambda item: (
                item.score,
                item.hop_count,
            ),
        )

    def select_best(
        self,
        paths: list[PhysicalPath],
    ) -> PhysicalPath | None:
        """
        Select the best EXECUTABLE path.

        Safety/trust eligibility has higher priority than
        hop count or score.

        If no candidate is executable, return None so the
        caller can fail closed rather than selecting a path
        that execution policy will reject later.
        """

        executable_paths = [
            path
            for path in paths
            if self._is_executable_path(
                path
            )
        ]

        if not executable_paths:
            return None

        ranked = self.rank(
            executable_paths
        )

        if not ranked:
            return None

        return ranked[0].path

    # ======================================================
    # Execution eligibility
    # ======================================================

    def _is_executable_path(
        self,
        path: PhysicalPath,
    ) -> bool:
        """
        A path is executable only when every physical join
        has an execution-approved validation status.

        A zero-join path is valid because it represents a
        single-table query.
        """

        return all(
            (
                join.validation_status
                or "unknown"
            )
            in self.EXECUTABLE_JOIN_STATUSES

            for join in path.joins
        )

    # ======================================================
    # Scoring
    # ======================================================

    def _score_path(
        self,
        path: PhysicalPath,
    ) -> RankedPhysicalPath:

        hop_count = len(
            path.joins
        )

        score = (
            hop_count
            * self.HOP_PENALTY
        )

        confirmed_joins = 0
        warning_joins = 0
        inferred_joins = 0

        for join in path.joins:

            status = (
                join.validation_status
                or "unknown"
            )

            penalty = (
                self.STATUS_PENALTIES.get(
                    status,
                    self.UNKNOWN_STATUS_PENALTY,
                )
            )

            score += penalty

            if status == "confirmed":

                confirmed_joins += 1

            elif status == "inferred":

                inferred_joins += 1

            else:

                warning_joins += 1

        return RankedPhysicalPath(
            path=path,
            score=score,
            hop_count=hop_count,
            confirmed_joins=confirmed_joins,
            warning_joins=warning_joins,
            inferred_joins=inferred_joins,
        )
