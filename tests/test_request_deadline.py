"""A7.1: one deadline for the whole question (unit level)."""
import threading
import time

import pytest

from observability.deadline import (DeadlineExceeded, cap_timeout, check_deadline,
                                    remaining_seconds, request_deadline)


def test_no_deadline_leaves_timeouts_unchanged():
    assert remaining_seconds() is None
    assert cap_timeout(5.0) == 5.0
    check_deadline()


def test_timeouts_are_capped_by_the_time_left():
    with request_deadline(1.0):
        assert 0 < cap_timeout(5.0) <= 1.0
        assert cap_timeout(0.2) == 0.2


def test_used_up_deadline_refuses_new_work():
    with request_deadline(0.0):
        with pytest.raises(DeadlineExceeded):
            cap_timeout(5.0)
        with pytest.raises(DeadlineExceeded):
            check_deadline()


def test_nested_deadline_cannot_extend_the_outer_one():
    # Floating-point rounding of "now + 0.1 - now" can add ~1e-13 when the
    # clock has not advanced (coarse on Windows); 1e-9 absorbs only that.
    rounding = 1e-9
    with request_deadline(0.5):
        with request_deadline(60):
            assert remaining_seconds() <= 0.5 + rounding
        with request_deadline(0.1):
            assert remaining_seconds() <= 0.1 + rounding
    assert remaining_seconds() is None


def test_deadline_is_restored_after_errors():
    with pytest.raises(RuntimeError):
        with request_deadline(1.0):
            raise RuntimeError
    assert remaining_seconds() is None


def test_concurrent_requests_do_not_share_deadlines():
    seen = {}

    def worker(name, seconds):
        with request_deadline(seconds):
            time.sleep(0.05)
            seen[name] = remaining_seconds()

    threads = [threading.Thread(target=worker, args=("short", 0.2)),
               threading.Thread(target=worker, args=("long", 30))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen["short"] < 0.2 < 29 < seen["long"]


def test_deadline_error_is_a_timeout_with_safe_text():
    error = DeadlineExceeded()
    assert isinstance(error, TimeoutError)
    assert "took too long" in str(error)
