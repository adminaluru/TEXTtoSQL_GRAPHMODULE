import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hybrid_contracts import AccessContext, ClarificationResponse
from clarification_state import (ClarificationChoice, ClarificationStateError,
                                 PendingSelectionStore)

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def access(subject="u1", tenant="tenant-a", policy="1"):
    return AccessContext(subject_id=subject, tenant_id=tenant, policy_version=policy)


def setup(**kwargs):
    clock = Clock()
    store = PendingSelectionStore(clock=clock, **kwargs)
    prompt = store.create(
        access=access(), conversation_id="c1", question="Which customer?", reason="ambiguous",
        choices=[ClarificationChoice("Acme LE-1", ("k1",)), ClarificationChoice("Acme LE-2", ("k2",))],
        payload={"plan": "original"})
    return store, prompt, clock


def answer(prompt, index=0, **kwargs):
    return ClarificationResponse(clarification_id=prompt.clarification_id,
                                 option_id=prompt.options[index].option_id, **kwargs)


def test_rightful_caller_gets_value_and_payload():
    store, prompt, _ = setup()
    selection = store.take(answer(prompt, 1), access=access(), conversation_id="c1")
    assert selection.value == ("k2",)
    assert selection.payload == {"plan": "original"}


def test_prompt_exposes_labels_but_not_values():
    _, prompt, _ = setup()
    dumped = prompt.model_dump_json()
    assert "Acme LE-1" in dumped
    assert "k1" not in dumped and "original" not in dumped
    assert prompt.expires_at == T0 + timedelta(minutes=10)


def test_option_and_clarification_ids_are_opaque_and_unique():
    _, first, _ = setup()
    _, second, _ = setup()
    ids = [o.option_id for o in first.options + second.options]
    assert len(set(ids)) == 4
    assert all(i not in ("0", "1", "k1", "k2") for i in ids)
    assert first.clarification_id != second.clarification_id


@pytest.mark.parametrize("caller,conversation", [
    (access(subject="u2"), "c1"),
    (access(tenant="tenant-b"), "c1"),
    (access(), "c2"),
    (access(policy="2"), "c1"),        # permissions changed since the prompt
])
def test_other_caller_cannot_take_or_consume(caller, conversation):
    store, prompt, _ = setup()
    with pytest.raises(ClarificationStateError, match="no longer available"):
        store.take(answer(prompt), access=caller, conversation_id=conversation)
    assert store.take(answer(prompt), access=access(), conversation_id="c1").value == ("k1",)


def test_replay_after_success_is_rejected():
    store, prompt, _ = setup()
    store.take(answer(prompt), access=access(), conversation_id="c1")
    with pytest.raises(ClarificationStateError, match="no longer available"):
        store.take(answer(prompt), access=access(), conversation_id="c1")


def test_expired_selection_is_rejected():
    store, prompt, clock = setup()
    clock.now = T0 + timedelta(minutes=10)
    with pytest.raises(ClarificationStateError, match="no longer available"):
        store.take(answer(prompt), access=access(), conversation_id="c1")


@pytest.mark.parametrize("response", [
    lambda p: ClarificationResponse(clarification_id=p.clarification_id, option_id="forged"),
    lambda p: ClarificationResponse(clarification_id=p.clarification_id, text="SYN-CUST-1"),
])
def test_unoffered_option_or_text_is_rejected_and_state_kept(response):
    store, prompt, _ = setup()
    with pytest.raises(ClarificationStateError, match="offered options"):
        store.take(response(prompt), access=access(), conversation_id="c1")
    assert store.take(answer(prompt), access=access(), conversation_id="c1").value == ("k1",)


def test_unknown_clarification_is_rejected():
    store, _, _ = setup()
    forged = ClarificationResponse(clarification_id="nope", option_id="x")
    with pytest.raises(ClarificationStateError, match="no longer available"):
        store.take(forged, access=access(), conversation_id="c1")


def test_capacity_evicts_oldest_pending():
    store, first, _ = setup(max_pending=1)
    second = store.create(access=access(), conversation_id="c1", question="Which?", reason="r",
                          choices=[ClarificationChoice("B", "kb")], payload=None)
    with pytest.raises(ClarificationStateError):
        store.take(answer(first), access=access(), conversation_id="c1")
    assert store.take(answer(second), access=access(), conversation_id="c1").value == "kb"


@pytest.mark.parametrize("kwargs", [{"choices": []}, {"conversation_id": " "}])
def test_invalid_create_is_rejected(kwargs):
    store = PendingSelectionStore()
    args = dict(access=access(), conversation_id="c1", question="Q?", reason="r",
                choices=[ClarificationChoice("A", 1)], payload=None)
    args.update(kwargs)
    with pytest.raises(ValueError):
        store.create(**args)


def test_package_imports_only_portable_dependencies():
    allowed = {"secrets", "threading", "collections", "dataclasses", "datetime", "typing",
               "hybrid_contracts"}
    src = Path(__file__).parents[1] / "src" / "clarification_state"
    for path in src.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert roots <= allowed, (path.name, roots)
