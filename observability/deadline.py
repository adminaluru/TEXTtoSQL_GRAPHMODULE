"""One deadline for a whole question (Step 7).

Each external call already has its own timeout (database statement, LLM
request). Without a shared deadline those add up: several LLM attempts plus
several queries could take minutes. The request deadline is set once per
question; every call then gets at most the time that is left.

    with request_deadline(60):             # orchestrator / adapter
        ...
        timeout = cap_timeout(5.0)         # executor, LLM client, Neo4j
        check_deadline()                   # between steps

It is stored in a context variable, so concurrent requests never see each
other's deadline.
"""
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_deadline: ContextVar[float | None] = ContextVar("flowone_request_deadline", default=None)

TOO_SLOW = "The question took too long to answer. Please try again or ask a narrower question."


class DeadlineExceeded(TimeoutError):
    """The request's total time budget is used up. Message is safe for users."""

    def __init__(self, message: str = TOO_SLOW):
        super().__init__(message)


@contextmanager
def request_deadline(seconds: float | None) -> Iterator[None]:
    """Set a deadline `seconds` from now; an existing, earlier deadline wins."""
    current = _deadline.get()
    new = time.monotonic() + seconds if seconds is not None else None
    effective = min(d for d in (current, new) if d is not None) if (current or new) else None
    token = _deadline.set(effective)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_seconds() -> float | None:
    """Seconds left, or None when no deadline is set."""
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def check_deadline() -> None:
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise DeadlineExceeded()


def cap_timeout(seconds: float) -> float:
    """The smaller of a call's own timeout and the time left; refuses when none is left."""
    remaining = remaining_seconds()
    if remaining is None:
        return seconds
    if remaining <= 0:
        raise DeadlineExceeded()
    return min(seconds, remaining)
