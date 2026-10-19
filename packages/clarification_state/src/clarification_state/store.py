"""Pending clarification selections (action A2.2).

A clarification offers opaque option IDs. The values behind them (record keys)
and the server-side payload never leave the store. A selection is accepted
only from the same subject, tenant, conversation and policy version that
received the prompt, before expiry, and only once.

Retry/replay rules:
- wrong caller, unknown, expired or already-used ID: "no longer available";
  a wrong caller never consumes or reveals the pending state;
- an option that was not offered, or free text: rejected, state kept so the
  rightful caller can retry until expiry;
- success consumes the state; replaying it is "no longer available".

In-memory and single-process. A shared store (e.g. database) must keep the
same semantics, including atomic take.
"""
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from hybrid_contracts import (AccessContext, ClarificationOption,
                              ClarificationPrompt, ClarificationResponse)

UNAVAILABLE = "This clarification is no longer available. Please ask the question again."
INVALID_CHOICE = "Please choose one of the offered options."


class ClarificationStateError(ValueError):
    """Safe to show to the caller."""


@dataclass(frozen=True)
class ClarificationChoice:
    label: str
    value: Any


@dataclass(frozen=True)
class Selection:
    value: Any
    payload: Any


@dataclass(frozen=True)
class _Pending:
    binding: tuple[str, str, str, str]
    expires_at: datetime
    values: dict[str, Any]
    payload: Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PendingSelectionStore:
    def __init__(self, ttl: timedelta = timedelta(minutes=10), max_pending: int = 1000,
                 clock: Callable[[], datetime] = _utcnow):
        if ttl <= timedelta(0) or max_pending < 1:
            raise ValueError("ttl and max_pending must be positive")
        self._ttl = ttl
        self._max_pending = max_pending
        self._clock = clock
        self._pending: OrderedDict[str, _Pending] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _binding(access: AccessContext, conversation_id: str) -> tuple[str, str, str, str]:
        if not conversation_id or not conversation_id.strip():
            raise ValueError("conversation_id is required")
        return (access.subject_id, access.tenant_id, conversation_id, access.policy_version)

    def create(self, *, access: AccessContext, conversation_id: str, question: str,
               reason: str, choices: Sequence[ClarificationChoice], payload: Any) -> ClarificationPrompt:
        if not choices:
            raise ValueError("A selection clarification needs at least one choice")
        binding = self._binding(access, conversation_id)
        now = self._clock()
        values = {secrets.token_urlsafe(12): c.value for c in choices}
        prompt = ClarificationPrompt(
            clarification_id=secrets.token_urlsafe(24),
            question=question,
            reason=reason,
            options=tuple(ClarificationOption(option_id=oid, label=c.label)
                          for oid, c in zip(values, choices)),
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._prune(now)
            while len(self._pending) >= self._max_pending:
                self._pending.popitem(last=False)
            self._pending[prompt.clarification_id] = _Pending(
                binding, prompt.expires_at, values, payload)
        return prompt

    def take(self, response: ClarificationResponse, *, access: AccessContext,
             conversation_id: str) -> Selection:
        binding = self._binding(access, conversation_id)
        with self._lock:
            now = self._clock()
            self._prune(now)
            pending = self._pending.get(response.clarification_id)
            if pending is None or not secrets.compare_digest(
                    "\x1f".join(pending.binding), "\x1f".join(binding)):
                raise ClarificationStateError(UNAVAILABLE)
            if response.option_id is None or response.option_id not in pending.values:
                raise ClarificationStateError(INVALID_CHOICE)
            del self._pending[response.clarification_id]
            return Selection(value=pending.values[response.option_id], payload=pending.payload)

    def _prune(self, now: datetime) -> None:
        for key in [k for k, p in self._pending.items() if p.expires_at <= now]:
            del self._pending[key]
