import logging
import time
import uuid

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


# ==========================================================
# Request correlation
#
# The HTTP layer sets the request id so the id returned to
# the client matches the FLOWONE_PERF log line.
# ==========================================================

request_id_var: ContextVar[str | None] = ContextVar(
    "flowone_request_id",
    default=None,
)


def new_request_id() -> str:

    return uuid.uuid4().hex[:12]


class PerfTrace:
    """
    Shared stage-timing helper for structured performance
    log lines.

    Usage:

        with PerfTrace(
            event="FLOWONE_PERF",
            id_field="request_id",
            stage_order=[...],
            logger=logger,
        ) as trace:

            with trace.stage("question_analyzer"):
                ...

    Emits exactly one line when the block exits:

        <event> <id_field>=<id> status=<success|failed>
        failed_stage=<stage|-> [<field>=<value> ...]
        <stage>_ms=<ms> ... total_ms=<ms>

    Semantics:

    - status is "success" when the block exits without an
      exception, otherwise "failed"
    - failed_stage is the most recently marked stage
    - a stage that raises records no timing
    - repeated timings for the same stage accumulate
    - fields are emitted in insertion order; None -> "-"

    Timing never changes business logic.
    """

    def __init__(
        self,
        event: str,
        id_field: str,
        stage_order: list[str],
        logger: logging.Logger,
        fields: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> None:

        self.id = trace_id or new_request_id()

        self.timings: dict[str, float] = {}

        self.fields: dict[str, Any] = dict(
            fields or {}
        )

        self.current_stage = "startup"

        self._event = event
        self._id_field = id_field
        self._stage_order = list(stage_order)
        self._logger = logger
        self._started = 0.0

    # ======================================================
    # Context manager
    # ======================================================

    def __enter__(self) -> "PerfTrace":

        self._started = time.perf_counter()

        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ) -> bool:

        self.timings["total"] = elapsed_ms(
            self._started
        )

        self._log(
            success=exc_type is None,
        )

        return False

    # ======================================================
    # Stages
    # ======================================================

    def mark(
        self,
        stage: str,
    ) -> None:
        """
        Record the stage that would be reported if the
        block failed now, without timing anything.
        """

        self.current_stage = stage

    @contextmanager
    def stage(
        self,
        name: str,
        mark: bool = True,
    ) -> Iterator[None]:
        """
        Time one stage.

        mark=False times work without changing the stage
        reported on failure.
        """

        if mark:
            self.current_stage = name

        started = time.perf_counter()

        yield

        self.timings[name] = (
            self.timings.get(name, 0.0)
            + elapsed_ms(started)
        )

    # ======================================================
    # Logging
    # ======================================================

    def _log(
        self,
        success: bool,
    ) -> None:

        stage_order = self._stage_order

        if "total" not in stage_order:
            stage_order = stage_order + ["total"]

        timing_text = " ".join(
            f"{stage}_ms={self.timings[stage]:.2f}"
            for stage in stage_order
            if stage in self.timings
        )

        parts = [
            self._event,
            f"{self._id_field}=%s",
            "status=%s",
            "failed_stage=%s",
        ]

        args: list[Any] = [
            self.id,
            "success" if success else "failed",
            "-" if success else self.current_stage,
        ]

        for name, value in self.fields.items():

            parts.append(f"{name}=%s")

            args.append(
                "-" if value is None else value
            )

        parts.append("%s")
        args.append(timing_text)

        self._logger.info(
            " ".join(parts),
            *args,
        )


def elapsed_ms(
    started: float,
) -> float:

    return round(
        (time.perf_counter() - started) * 1000,
        2,
    )
