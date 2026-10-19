"""Split monetary aggregates by currency (decision D-004).

SUM, AVG, MIN and MAX over a financial measure (ontology Column.category
contains "financial") are grouped by the measure's currency, so different
currencies are never combined. The currency source is reviewed data
(measure_currency.json): the row's own currency column, or the currency of
one linked table reached through a single confirmed many-to-one join.

Fails closed: a financial measure with no known currency source, or a plan
that already reaches the currency table by another path, is refused.
"""
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from planning.errors import UnsupportedPredicateError
from planning.models import QueryGrouping, QueryJoin, QueryPlan, QueryProjection

DEFAULT_PATH = Path(__file__).with_name("measure_currency.json")

CURRENCY_ALIAS = "currency"

MONEY_FUNCTIONS = {"SUM", "AVG", "MIN", "MAX"}

UNKNOWN_CURRENCY = (
    "I can't determine the currency of this amount, so I can't "
    "calculate it without risking combining different currencies."
)
MIXED_MEASURES = (
    "I can't combine amounts from different kinds of records in one "
    "answer yet. Please ask about one kind of amount at a time."
)


class CurrencySource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    column: str | None = None
    via: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.column is None) == (self.via is None):
            raise ValueError("A currency source needs exactly one of column or via")
        return self


def load_currency_sources(path: Path = DEFAULT_PATH) -> dict[str, CurrencySource]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    sources = {table: CurrencySource.model_validate(item)
               for table, item in raw.items() if not table.startswith("_")}
    for table, source in sources.items():
        if source.via is not None and (sources.get(source.via) is None
                                       or sources[source.via].column is None):
            raise ValueError(f"{table} takes its currency from a table without a currency column.")
    return sources


class CurrencySplitter:
    def __init__(self, ontology, sources: dict[str, CurrencySource]):
        self._ontology = ontology
        self._sources = sources

    def apply(self, plan: QueryPlan) -> QueryPlan:
        money = [a for a in plan.aggregations
                 if a.function in MONEY_FUNCTIONS and self._is_financial(a.table, a.column)]
        if not money or plan.currency_alias is not None:
            return plan
        if len({a.table for a in money}) != 1:
            raise UnsupportedPredicateError(MIXED_MEASURES)
        table = money[0].table
        source = self._sources.get(table)
        if source is None:
            raise UnsupportedPredicateError(UNKNOWN_CURRENCY)
        currency_table = source.via or table
        currency_column = self._sources[currency_table].column

        split = plan.model_copy(deep=True)
        if currency_table != table:
            join = self._currency_join(table, currency_table)
            if currency_table in split.tables:
                # The plan already reaches the currency table; it must be
                # through this exact relationship, or the currency would
                # belong to a different record.
                if not any(self._same_join(j, join) for j in split.joins):
                    raise UnsupportedPredicateError(UNKNOWN_CURRENCY)
            else:
                split.tables.append(currency_table)
                split.joins.append(join)
        if any(p.alias == CURRENCY_ALIAS for p in split.projections):
            raise ValueError("Projection alias collides with the currency alias.")
        split.groupings.append(QueryGrouping(table=currency_table, column=currency_column))
        split.projections.append(QueryProjection(table=currency_table, column=currency_column,
                                                 alias=CURRENCY_ALIAS))
        split.currency_alias = CURRENCY_ALIAS
        return split

    def _is_financial(self, table: str, column: str) -> bool:
        for c in self._ontology.get_table_columns(table):
            if c.name == column:
                return "financial" in (c.category or "").lower()
        return False

    def _currency_join(self, table: str, currency_table: str) -> QueryJoin:
        # LEFT: a row whose linked record is missing still appears, with an
        # unknown currency, instead of silently leaving the total.
        keys = [c.name for c in self._ontology.get_table_columns(currency_table) if c.is_primary_key]
        joins = [
            path.joins[0]
            for path in self._ontology.find_physical_paths(table, currency_table, max_hops=1)
            if len(path.joins) == 1
            and path.joins[0].source_table == table
            and path.joins[0].target_table == currency_table
            and path.joins[0].validation_status == "confirmed"
            and [path.joins[0].target_column] == keys
        ]
        if len(joins) != 1:
            raise UnsupportedPredicateError(UNKNOWN_CURRENCY)
        return QueryJoin(left_table=table, left_column=joins[0].source_column,
                         right_table=currency_table, right_column=joins[0].target_column,
                         join_type="LEFT", validation_status="confirmed")

    @staticmethod
    def _same_join(existing: QueryJoin, expected: QueryJoin) -> bool:
        forward = (existing.left_table, existing.left_column, existing.right_table, existing.right_column)
        backward = (existing.right_table, existing.right_column, existing.left_table, existing.left_column)
        target = (expected.left_table, expected.left_column, expected.right_table, expected.right_column)
        return target in (forward, backward)
