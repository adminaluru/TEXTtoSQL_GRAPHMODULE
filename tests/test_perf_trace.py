import logging
import re

import pytest

from observability.perf import PerfTrace


def _trace(logger, **kwargs):

    return PerfTrace(
        event="FLOWONE_TEST_PERF",
        id_field="request_id",
        stage_order=["first", "second"],
        logger=logger,
        **kwargs,
    )


def test_success_line_matches_legacy_format(caplog):

    logger = logging.getLogger("perf-trace-test")

    with caplog.at_level(logging.INFO, logger="perf-trace-test"):

        with _trace(logger) as trace:

            with trace.stage("second"):
                pass

            with trace.stage("first"):
                pass

    line = caplog.records[-1].getMessage()

    assert re.fullmatch(
        r"FLOWONE_TEST_PERF request_id=[0-9a-f]{12} "
        r"status=success failed_stage=- "
        r"first_ms=\d+\.\d{2} second_ms=\d+\.\d{2} "
        r"total_ms=\d+\.\d{2}",
        line,
    )


def test_failure_reports_stage_and_skips_its_timing(caplog):

    logger = logging.getLogger("perf-trace-test")

    with caplog.at_level(logging.INFO, logger="perf-trace-test"):

        with pytest.raises(RuntimeError):

            with _trace(logger) as trace:

                with trace.stage("first"):
                    pass

                with trace.stage("second"):
                    raise RuntimeError("boom")

    line = caplog.records[-1].getMessage()

    assert "status=failed failed_stage=second " in line
    assert "first_ms=" in line
    assert "second_ms=" not in line
    assert "total_ms=" in line


def test_fields_accumulation_and_unmarked_stages(caplog):

    logger = logging.getLogger("perf-trace-test")

    with caplog.at_level(logging.INFO, logger="perf-trace-test"):

        with pytest.raises(ValueError):

            with _trace(
                logger,
                fields={"count": 0, "size": None},
            ) as trace:

                trace.mark("first")

                for _ in range(3):

                    trace.fields["count"] += 1

                    with trace.stage("second", mark=False):
                        pass

                raise ValueError("fail")

    line = caplog.records[-1].getMessage()

    assert "failed_stage=first count=3 size=- " in line
    assert line.count("second_ms=") == 1
