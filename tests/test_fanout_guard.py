from ontology.models import ColumnMetadata

from planning.fanout_guard import FanOutGuard

from planning.models import (
    QueryAggregation,
    QueryJoin,
    QueryPlan,
)


CUSTOMER = "public.customer"
INVOICE = "public.invoice"
PAYMENT = "public.payment"


class FakeOntology:
    """Every table has a single-column primary key `id`."""

    def get_table_columns(self, table_key):

        return [
            ColumnMetadata(
                key=f"{table_key}.{name}",
                name=name,
                qualified_name=f"{table_key}.{name}",
                table_key=table_key,
                table_name=table_key.split(".")[1],
                is_primary_key=(name == "id"),
            )
            for name in ("id", "customer_id", "invoice_id", "amount")
        ]


def _plan(function, measure_table, joins, tables):

    return QueryPlan.model_construct(
        intent="aggregate",
        tables=tables,
        joins=joins,
        aggregations=[
            QueryAggregation(
                function=function,
                table=measure_table,
                column="amount",
                alias="value",
            )
        ],
    )


INVOICE_TO_CUSTOMER = QueryJoin(
    left_table=INVOICE,
    left_column="customer_id",
    right_table=CUSTOMER,
    right_column="id",
)

PAYMENT_TO_INVOICE = QueryJoin(
    left_table=PAYMENT,
    left_column="invoice_id",
    right_table=INVOICE,
    right_column="id",
)


def test_many_to_one_joins_are_allowed():

    # SUM(payment.amount) -> invoice -> customer: every hop
    # lands on a primary key, so no payment is repeated.
    plan = _plan(
        "SUM",
        PAYMENT,
        [PAYMENT_TO_INVOICE, INVOICE_TO_CUSTOMER],
        [PAYMENT, INVOICE, CUSTOMER],
    )

    assert FanOutGuard(FakeOntology()).check(plan) == []


def test_one_to_many_join_is_blocked():

    # SUM(invoice.amount) joined to payments repeats each
    # invoice once per payment.
    plan = _plan(
        "SUM",
        INVOICE,
        [PAYMENT_TO_INVOICE],
        [INVOICE, PAYMENT],
    )

    violations = FanOutGuard(FakeOntology()).check(plan)

    assert violations == [
        "SUM over public.invoice would be multiplied by the "
        "one-to-many join public.invoice.id -> "
        "public.payment.invoice_id"
    ]


def test_fan_out_further_down_the_path_is_blocked():

    # SUM(customer.amount) -> invoice (one-to-many) -> payment
    plan = _plan(
        "AVG",
        CUSTOMER,
        [INVOICE_TO_CUSTOMER, PAYMENT_TO_INVOICE],
        [CUSTOMER, INVOICE, PAYMENT],
    )

    violations = FanOutGuard(FakeOntology()).check(plan)

    assert len(violations) == 2
    assert all(v.startswith("AVG over public.customer") for v in violations)


def test_count_is_guarded_but_min_max_are_not():

    joins = [PAYMENT_TO_INVOICE]
    tables = [INVOICE, PAYMENT]

    guard = FanOutGuard(FakeOntology())

    assert guard.check(_plan("COUNT", INVOICE, joins, tables))
    assert guard.check(_plan("MIN", INVOICE, joins, tables)) == []
    assert guard.check(_plan("MAX", INVOICE, joins, tables)) == []


def test_single_table_aggregate_is_allowed():

    plan = _plan("SUM", INVOICE, [], [INVOICE])

    assert FanOutGuard(FakeOntology()).check(plan) == []
