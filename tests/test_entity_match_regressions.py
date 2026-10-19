from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from planning.entity_match_guard import EntityMatchGuard, EntityMatchResult
from planning.models import QueryExistence, QueryFilter, QueryPlan
from sql_compiler.compiler import DeterministicSQLCompiler
from sql_validator.validator import SQLValidator
from sql_guardrails.guardrails import SQLGuardrails


def name(table="public.customers"):
    return QueryFilter(table=table, column="name", operator="ILIKE", value="%Blue%")


def setup_guard(rows=(), keys=("id",), truncated=False):
    ontology = Mock()
    ontology.get_table_columns.return_value = [SimpleNamespace(name=k, is_primary_key=True) for k in keys]
    executor = Mock()
    executor.execute.return_value = SimpleNamespace(rows=list(rows), truncated=truncated)
    guard = EntityMatchGuard(ontology, DeterministicSQLCompiler(), SQLValidator(), SQLGuardrails(), executor)
    return guard, ontology, executor


def plan():
    return QueryPlan(intent="aggregate", tables=["public.customers"], filters=[name()])


def test_unique_match_is_pinned_without_mutating_input():
    guard, _, executor = setup_guard([{"entity_key_0": 7}])
    original = plan()
    result = guard.resolve(original)
    assert result.clarification is None
    assert len(original.filters) == 1
    assert result.plan.filters[-1].column == "id"
    assert result.plan.filters[-1].value == 7
    assert result.plan.filters[0] == original.filters[0]
    compiled = executor.execute.call_args.kwargs["compiled"]
    assert "LIMIT 2" in compiled.sql
    assert "%Blue%" not in compiled.sql
    assert compiled.parameters == ["%Blue%"]


@pytest.mark.parametrize("rows,truncated", [
    ([{"entity_key_0": 1}, {"entity_key_0": 2}], False),
    ([{"entity_key_0": 1}], True),
])
def test_multiple_or_incomplete_matches_require_clarification(rows, truncated):
    guard, _, _ = setup_guard(rows, truncated=truncated)
    original = plan()
    result = guard.resolve(original)
    assert result.clarification is not None
    assert result.plan == original
    assert "identifier" in result.clarification
    assert "public.customers" not in result.clarification


def test_zero_matches_require_correction():
    guard, _, _ = setup_guard()
    assert "could not find" in guard.resolve(plan()).clarification


def test_missing_pk_never_executes_lookup():
    guard, _, executor = setup_guard(keys=())
    assert guard.resolve(plan()).clarification
    executor.execute.assert_not_called()


def test_composite_key_pins_every_part():
    guard, _, _ = setup_guard([{"entity_key_0": 4, "entity_key_1": 9}], keys=("tenant_id", "id"))
    result = guard.resolve(plan())
    assert [(f.column, f.value) for f in result.plan.filters[1:]] == [("tenant_id", 4), ("id", 9)]


@pytest.mark.parametrize("row", [{}, {"entity_key_0": None}])
def test_invalid_identity_fails_closed(row):
    guard, _, _ = setup_guard([row])
    with pytest.raises(ValueError, match="invalid primary key"):
        guard.resolve(plan())


def test_no_name_filter_preserves_plan_without_lookup():
    guard, ontology, executor = setup_guard()
    original = QueryPlan(intent="list", tables=["public.customers"])
    assert guard.resolve(original).plan == original
    ontology.get_table_columns.assert_not_called()
    executor.execute.assert_not_called()


def test_same_table_scope_filters_are_kept():
    guard, _, executor = setup_guard([{"entity_key_0": 8}])
    original = plan()
    original.filters.append(QueryFilter(table="public.customers", column="tenant_id", operator="=", value=42))
    guard.resolve(original)
    assert executor.execute.call_args.kwargs["compiled"].parameters == ["%Blue%", 42]


def test_existence_name_filter_is_pinned_in_its_own_scope():
    guard, _, _ = setup_guard([{"entity_key_0": 8}])
    original = QueryPlan(intent="list", tables=["public.invoices"], existence_conditions=[
        QueryExistence(anchor_table="public.invoices", tables=["public.customers"], filters=[name()])])
    result = guard.resolve(original)
    assert not result.plan.filters
    assert result.plan.existence_conditions[0].filters[-1].value == 8
    assert len(original.existence_conditions[0].filters) == 1


def test_multiple_name_predicates_use_one_probe():
    guard, _, executor = setup_guard([{"entity_key_0": 8}])
    original = plan()
    original.filters.append(name())
    guard.resolve(original)
    assert executor.execute.call_count == 1


@pytest.mark.parametrize("blocked", ["validation", "guardrails"])
def test_rejected_probe_never_reaches_database(blocked):
    guard, _, executor = setup_guard()
    if blocked == "validation":
        guard._validator = Mock()
        guard._validator.validate.return_value = SimpleNamespace(valid=False)
        error = ValueError
    else:
        guard._guardrails = Mock()
        guard._guardrails.evaluate.return_value = SimpleNamespace(allowed=False)
        error = PermissionError
    with pytest.raises(error):
        guard.resolve(plan())
    executor.execute.assert_not_called()


def test_orchestrator_clarifies_without_answer_execution_or_repair():
    from agent.orchestrator import ResearchAgentOrchestrator
    agent = ResearchAgentOrchestrator.__new__(ResearchAgentOrchestrator)
    interpretation = SimpleNamespace(query_plan=plan(), graph_plan=None)
    agent._interpret_with_repair = Mock(return_value=interpretation)
    agent._currency_splitter = None
    agent._empty_group_policy = None
    agent._missing_value_policy = None
    agent._value_domain_guard = None
    agent._fanout_guard = Mock()
    agent._fanout_guard.find.return_value = []
    agent._entity_match_guard = Mock()
    agent._entity_match_guard.resolve.return_value = EntityMatchResult(plan(), "Please identify one customer.")
    agent._execute = Mock()
    response = agent.ask("What has Blue paid?")
    assert response.answer == "Please identify one customer."
    agent._execute.assert_not_called()
    assert agent._interpret_with_repair.call_count == 1


def test_orchestrator_executes_the_pinned_plan_for_unique_match():
    from agent.orchestrator import ResearchAgentOrchestrator
    agent = ResearchAgentOrchestrator.__new__(ResearchAgentOrchestrator)
    interpretation = SimpleNamespace(query_plan=plan(), graph_plan=None)
    agent._interpret_with_repair = Mock(return_value=interpretation)
    agent._currency_splitter = None
    agent._empty_group_policy = None
    agent._missing_value_policy = None
    agent._value_domain_guard = None
    agent._fanout_guard = Mock()
    agent._fanout_guard.find.return_value = []
    guard, _, _ = setup_guard([{"entity_key_0": 7}])
    agent._entity_match_guard = guard
    answer = SimpleNamespace(warnings=[])
    agent._execute = Mock(return_value=answer)
    assert agent.ask("What has Blue paid?") is answer
    executed = agent._execute.call_args.kwargs["interpretation"].query_plan
    assert executed.filters[-1].value == 7
    assert agent._execute.call_count == 1


# Decision D-001: resolve one customer inside the caller's active company and
# clarify ambiguity within that company. The physical tenant column is still
# undecided (D-002), so these tests use a synthetic policy double.

def company(table="public.customers", value=527):
    return QueryFilter(table=table, column="company_id", operator="=", value=value)


def scoped_guard(rows, scope):
    guard, ontology, executor = setup_guard(rows)
    policy = Mock()
    policy.filters_for.side_effect = lambda table: scope(table)
    guard._scope_policy = policy
    return guard, executor, policy


def test_scope_predicates_restrict_probe_and_answer():
    guard, executor, policy = scoped_guard([{"entity_key_0": 7}], lambda t: [company(t)])
    result = guard.resolve(plan())
    policy.filters_for.assert_called_once_with("public.customers")
    assert executor.execute.call_args.kwargs["compiled"].parameters == ["%Blue%", 527]
    assert [(f.column, f.value) for f in result.plan.filters[1:]] == [("company_id", 527), ("id", 7)]


def test_ambiguity_inside_active_company_still_clarifies():
    guard, _, _ = scoped_guard([{"entity_key_0": 1}, {"entity_key_0": 2}], lambda t: [company(t)])
    original = plan()
    result = guard.resolve(original)
    assert result.clarification is not None
    assert result.plan == original


def test_no_match_inside_active_company_does_not_leak_other_companies():
    guard, _, _ = scoped_guard([], lambda t: [company(t)])
    assert "could not find" in guard.resolve(plan()).clarification


def test_scope_predicate_for_another_table_fails_closed():
    guard, executor, _ = scoped_guard([{"entity_key_0": 7}], lambda t: [company("public.invoices")])
    with pytest.raises(ValueError, match="another table"):
        guard.resolve(plan())
    executor.execute.assert_not_called()


def test_existence_scope_is_restricted_for_its_own_table():
    guard, executor, _ = scoped_guard([{"entity_key_0": 8}], lambda t: [company(t, 528)])
    original = QueryPlan(intent="list", tables=["public.invoices"], existence_conditions=[
        QueryExistence(anchor_table="public.invoices", tables=["public.customers"], filters=[name()])])
    result = guard.resolve(original)
    assert executor.execute.call_args.kwargs["compiled"].parameters == ["%Blue%", 528]
    assert [(f.column, f.value) for f in result.plan.existence_conditions[0].filters[1:]] == [
        ("company_id", 528), ("id", 8)]
    assert not result.plan.filters


# A2.2 / D-003: labelled choices (name + legal entity) and re-checked selections.

from planning.entity_match_guard import ChoiceLabel

LABELS = {"public.customers": [ChoiceLabel(column="name"),
                               ChoiceLabel(table="public.entities", column="name")]}


def join(status="confirmed", target_column="id"):
    return SimpleNamespace(joins=[SimpleNamespace(
        source_table="public.customers", target_table="public.entities",
        source_column="entity_id", target_column=target_column, validation_status=status)])


def choice_guard(*results, paths=None, scope=None, max_choices=10):
    guard, ontology, executor = setup_guard()
    pk = lambda name: [SimpleNamespace(name=name, is_primary_key=True)]
    ontology.get_table_columns.side_effect = lambda t: pk("id")
    ontology.find_physical_paths.return_value = [join()] if paths is None else paths
    executor.execute.side_effect = [SimpleNamespace(rows=list(r), truncated=False) for r in results]
    guard._choice_labels = LABELS
    guard._max_choices = max_choices
    if scope:
        guard._scope_policy = Mock()
        guard._scope_policy.filters_for.side_effect = lambda t: [company(t)]
    return guard, executor


AMBIGUOUS_ROWS = [{"entity_key_0": 1}, {"entity_key_0": 2}]
CHOICES = [{"entity_key_0": 2, "choice_label_0": "Blue LE-2", "choice_label_1": "Acme Secondary"},
           {"entity_key_0": 1, "choice_label_0": "Blue LE-1", "choice_label_1": "Acme Primary"}]


def test_ambiguity_lists_labelled_sorted_choices_inside_scope():
    guard, executor = choice_guard(AMBIGUOUS_ROWS, CHOICES, scope=True)
    original = plan()
    result = guard.resolve(original, list_choices=True)
    assert result.plan == original
    assert result.location == (0, "public.customers")
    assert [(c.keys, c.label) for c in result.candidates] == [
        ((1,), "Blue LE-1 — Acme Primary"), ((2,), "Blue LE-2 — Acme Secondary")]
    compiled = executor.execute.call_args.kwargs["compiled"]
    assert "LEFT JOIN" in compiled.sql and "LIMIT 11" in compiled.sql
    assert compiled.parameters == ["%Blue%", 527]


def test_choices_are_not_listed_unless_requested():
    guard, executor = choice_guard(AMBIGUOUS_ROWS)
    result = guard.resolve(plan())
    assert result.clarification and not result.candidates
    assert executor.execute.call_count == 1


def test_too_many_matches_are_not_listed():
    rows = [{"entity_key_0": i, "choice_label_0": f"B{i}", "choice_label_1": "E"} for i in range(3)]
    guard, _ = choice_guard(AMBIGUOUS_ROWS, rows, max_choices=2)
    result = guard.resolve(plan(), list_choices=True)
    assert "too many" in result.clarification and not result.candidates


def test_unlabelled_choice_falls_back_to_plain_clarification():
    rows = [{"entity_key_0": 1, "choice_label_0": None, "choice_label_1": None},
            {"entity_key_0": 2, "choice_label_0": "B", "choice_label_1": "E"}]
    guard, _ = choice_guard(AMBIGUOUS_ROWS, rows)
    result = guard.resolve(plan(), list_choices=True)
    assert result.clarification and not result.candidates


@pytest.mark.parametrize("paths", [[], [join(status="proposed")], [join(target_column="code")],
                                   [join(), join()]])
def test_label_table_needs_one_confirmed_join_to_its_key(paths):
    guard, executor = choice_guard(AMBIGUOUS_ROWS, paths=paths)
    with pytest.raises(ValueError, match="single confirmed"):
        guard.resolve(plan(), list_choices=True)
    assert executor.execute.call_count == 1


def test_selection_is_rechecked_with_name_scope_and_key_then_pinned():
    guard, executor = choice_guard([{"entity_key_0": 2}], scope=True)
    original = plan()
    result = guard.resolve(original, selections={(0, "public.customers"): (2,)})
    assert result.clarification is None
    assert executor.execute.call_args.kwargs["compiled"].parameters == ["%Blue%", 527, 2]
    assert [(f.column, f.value) for f in result.plan.filters] == [
        ("name", "%Blue%"), ("company_id", 527), ("id", 2)]
    assert original.filters == [name()]


def test_selection_no_longer_matching_or_permitted_is_refused():
    guard, _ = choice_guard([], scope=True)
    result = guard.resolve(plan(), selections={(0, "public.customers"): (2,)})
    assert "no longer available" in result.clarification


def test_selection_with_wrong_key_shape_fails_closed():
    guard, executor = choice_guard()
    with pytest.raises(ValueError, match="does not match"):
        guard.resolve(plan(), selections={(0, "public.customers"): (1, 2)})
    executor.execute.assert_not_called()
