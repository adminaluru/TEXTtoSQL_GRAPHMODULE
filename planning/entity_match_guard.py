"""Portable preflight for the POC's name filters (resolved as ILIKE).

No LLM, API, or database driver dependency. Collaborators are injected.
Call after access policy has scoped the plan and database credentials.
This component does not implement tenant or field authorization: an injected
EntityScopePolicy supplies the active-company row predicates (decision D-001).

When a name matches several records as a substring, a whole-name match
(case-insensitive) is preferred: exactly one is pinned, several narrow the
choices, none keeps the substring result.

Ambiguous names can be answered with labelled choices (decision D-003: name
and legal entity). A selected record is looked up again with the original
name, the current scope and its key before it is pinned, so a choice that is
no longer permitted or no longer matches is refused rather than trusted.
"""
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict

from planning.models import QueryFilter, QueryJoin, QueryPlan, QueryProjection

AMBIGUOUS = ("This name matches more than one record. "
             "Please provide the full unique name or record identifier.")
TOO_MANY = ("This name matches too many records to list. "
            "Please provide a more specific name or record identifier.")
NOT_FOUND = ("I could not find a record matching this name. "
             "Please check the name or provide a record identifier.")
SELECTION_GONE = "The selected record is no longer available. Please ask the question again."

# (scope index, table): scope 0 is the plan's own filters, 1.. are existence conditions.
EntityLocation = tuple[int, str]


class EntityScopePolicy(Protocol):
    """Trusted row scope (e.g. the caller's active company) for a table.

    Must be derived from verified caller identity, never from question text.
    """

    def filters_for(self, table: str) -> list[QueryFilter]: ...


class ChoiceLabel(BaseModel):
    """A label column for clarification choices.

    table=None means the entity table itself; otherwise a table reached by
    one confirmed many-to-one join from the entity table.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    column: str
    table: str | None = None


@dataclass(frozen=True)
class EntityCandidate:
    keys: tuple[Any, ...]
    label: str


@dataclass(frozen=True)
class EntityMatchResult:
    plan: QueryPlan
    clarification: str | None = None
    candidates: tuple[EntityCandidate, ...] = ()
    location: EntityLocation | None = None


class EntityMatchGuard:
    def __init__(self, ontology, compiler, validator, guardrails, executor,
                 scope_policy: EntityScopePolicy | None = None,
                 choice_labels: Mapping[str, Sequence[ChoiceLabel]] | None = None,
                 max_choices: int = 10):
        if max_choices < 1:
            raise ValueError("max_choices must be positive")
        self._ontology = ontology
        self._compiler = compiler
        self._validator = validator
        self._guardrails = guardrails
        self._executor = executor
        self._scope_policy = scope_policy
        self._choice_labels = dict(choice_labels or {})
        self._max_choices = max_choices

    def resolve(self, plan: QueryPlan,
                selections: Mapping[EntityLocation, tuple[Any, ...]] | None = None,
                list_choices: bool = False) -> EntityMatchResult:
        selections = selections or {}
        # Copy so failed/ambiguous probes never mutate the caller's plan.
        resolved = plan.model_copy(deep=True)
        scopes = [resolved.filters]
        scopes.extend(condition.filters for condition in resolved.existence_conditions)
        for index, filters in enumerate(scopes):
            tables = dict.fromkeys(f.table for f in filters if f.operator == "ILIKE")
            for table in tables:
                # Scope first: matches outside the active company are never
                # probed, counted as ambiguous or offered as choices.
                scope = self._scope_filters(table)
                local_filters = [f for f in filters if f.table == table] + scope
                keys = [c for c in self._ontology.get_table_columns(table)
                        if c.is_primary_key]
                if not keys:
                    return EntityMatchResult(plan, "I cannot safely identify this name. "
                                             "Please provide a unique record identifier.")
                selected = selections.get((index, table))
                if selected is not None:
                    if len(selected) != len(keys):
                        raise ValueError("Selected entity key does not match the table key.")
                    local_filters += self._key_filters(table, keys, selected)
                aliases = [f"entity_key_{i}" for i in range(len(keys))]
                probe = QueryPlan(
                    intent="list", tables=[table], filters=local_filters,
                    projections=[QueryProjection(table=table, column=c.name, alias=a)
                                 for c, a in zip(keys, aliases)], limit=2,
                )
                result = self._run(probe)
                if (result.truncated or len(result.rows) > 1) and selected is None:
                    # Prefer a whole-name match (case-insensitive) over
                    # substring matches: "LE-1001" must not be ambiguous with
                    # "LE-10010". Several exact matches narrow the choices.
                    exact_filters = self._exact_name_filters(local_filters)
                    if exact_filters is not None:
                        exact = self._run(probe.model_copy(update={"filters": exact_filters}))
                        if exact.rows:
                            result, local_filters = exact, exact_filters
                if result.truncated or len(result.rows) > 1:
                    if list_choices and table in self._choice_labels:
                        return self._choices(plan, (index, table), local_filters, keys)
                    return EntityMatchResult(plan, AMBIGUOUS)
                if not result.rows:
                    return EntityMatchResult(plan, SELECTION_GONE if selected is not None else NOT_FOUND)
                row_keys = self._row_keys(result.rows[0], aliases)
                # Keep the original name and all other predicates, adding a PK
                # constraint so a newly inserted matching name cannot broaden
                # the subsequent answer query. Composite keys are supported.
                filters.extend(scope)
                filters.extend(self._key_filters(table, keys, row_keys))
        return EntityMatchResult(resolved)

    def _choices(self, plan: QueryPlan, location: EntityLocation,
                 local_filters: list[QueryFilter], keys) -> EntityMatchResult:
        table = location[1]
        aliases = [f"entity_key_{i}" for i in range(len(keys))]
        projections = [QueryProjection(table=table, column=c.name, alias=a)
                       for c, a in zip(keys, aliases)]
        tables, joins, label_aliases = [table], [], []
        for i, label in enumerate(self._choice_labels[table]):
            source = label.table or table
            if source != table and source not in tables:
                joins.append(self._label_join(table, source))
                tables.append(source)
            label_aliases.append(f"choice_label_{i}")
            projections.append(QueryProjection(table=source, column=label.column,
                                               alias=label_aliases[-1]))
        probe = QueryPlan(intent="list", tables=tables, joins=joins, filters=local_filters,
                          projections=projections, limit=self._max_choices + 1)
        result = self._run(probe)
        if result.truncated or len(result.rows) > self._max_choices:
            return EntityMatchResult(plan, TOO_MANY)
        candidates = [
            EntityCandidate(
                keys=self._row_keys(row, aliases),
                label=" — ".join(str(row[a]) for a in label_aliases if row.get(a) not in (None, "")),
            )
            for row in result.rows
        ]
        if any(not c.label for c in candidates):
            # An unlabelled choice cannot be told apart safely.
            return EntityMatchResult(plan, AMBIGUOUS)
        candidates.sort(key=lambda c: c.label)
        return EntityMatchResult(plan, AMBIGUOUS, tuple(candidates), location)

    def _label_join(self, table: str, related: str) -> QueryJoin:
        # Exactly one direct, confirmed join from the entity's foreign key to
        # the related table's single-column primary key: at most one label
        # row per candidate, so choices are never duplicated.
        related_keys = [c.name for c in self._ontology.get_table_columns(related)
                        if c.is_primary_key]
        joins = [
            path.joins[0] for path in self._ontology.find_physical_paths(table, related, max_hops=1)
            if len(path.joins) == 1
            and path.joins[0].source_table == table
            and path.joins[0].target_table == related
            and path.joins[0].validation_status == "confirmed"
            and [path.joins[0].target_column] == related_keys
        ]
        if len(joins) != 1:
            raise ValueError("Choice label table has no single confirmed many-to-one join.")
        join = joins[0]
        return QueryJoin(left_table=table, left_column=join.source_column,
                         right_table=related, right_column=join.target_column,
                         join_type="LEFT", validation_status="confirmed")

    def _run(self, probe: QueryPlan):
        compiled = self._compiler.compile(probe)
        validation = self._validator.validate(compiled=compiled, plan=probe)
        if not validation.valid:
            raise ValueError("Entity identity lookup failed SQL validation.")
        approval = self._guardrails.evaluate(
            compiled=compiled, plan=probe, validation=validation)
        if not approval.allowed:
            raise PermissionError("Entity identity lookup was blocked by guardrails.")
        return self._executor.execute(
            compiled=compiled, validation=validation, guardrails=approval)

    @staticmethod
    def _row_keys(row, aliases) -> tuple[Any, ...]:
        if any(a not in row or row[a] is None for a in aliases):
            raise ValueError("Entity identity lookup returned an invalid primary key.")
        return tuple(row[a] for a in aliases)

    @classmethod
    def _exact_name_filters(cls, filters: list[QueryFilter]) -> list[QueryFilter] | None:
        # "%value%" -> "value": ILIKE without wildcards is a case-insensitive
        # whole-value match. None when no filter can be narrowed this way.
        exact, changed = [], False
        for f in filters:
            inner = cls._unwrap_contains(f.value) if f.operator == "ILIKE" else None
            if inner is not None:
                f, changed = f.model_copy(update={"value": inner}), True
            exact.append(f)
        return exact if changed else None

    @staticmethod
    def _unwrap_contains(value: Any) -> str | None:
        if not isinstance(value, str) or len(value) < 3 or value[0] != "%" or value[-1] != "%":
            return None
        inner = value[1:-1]
        i = 0
        while i < len(inner):
            if inner[i] == "\\":
                if i + 1 == len(inner):
                    return None  # the closing "%" was escaped, not a wildcard
                i += 2
            elif inner[i] in "%_":
                return None  # an unescaped wildcard inside: not a plain contains
            else:
                i += 1
        return inner

    @staticmethod
    def _key_filters(table, keys, values) -> list[QueryFilter]:
        return [QueryFilter(table=table, column=c.name, operator="=", value=v)
                for c, v in zip(keys, values)]

    def _scope_filters(self, table: str) -> list[QueryFilter]:
        if self._scope_policy is None:
            return []
        scope = list(self._scope_policy.filters_for(table))
        if any(f.table != table for f in scope):
            raise ValueError("Entity scope policy returned a predicate for another table.")
        return scope
