"""
Column scoring must not depend on set iteration order.

Defect found 2026-10-15: "invoice status" resolved to inv_status in most
processes but was "ambiguous" (inv_status / transaction_status /
approval_status) in others (1 of 6 runs). The description bonus was added
to a running maximum inside the loop over attribute variants, a set whose
order changes with Python's per-process hash seed, so the final score
depended on that order.
"""

from itertools import permutations
from types import SimpleNamespace

import pytest

from planning.column_resolver import ColumnResolver


def column(name, description, category="dimension", data_type="VARCHAR(50)"):
    return SimpleNamespace(name=name, description=description, category=category,
                           data_type=data_type, key=f"public.t.{name}", ordinal=1,
                           is_primary_key=False, semantic_role=None, projection_role=None)


COLUMNS = [
    column("inv_status", "Invoice status. Sample: OPEN, CLOSE."),
    column("transaction_status", "Payment/transaction status of the invoice."),
    column("approval_status", "Approval workflow status."),
    column("amount", "Invoice amount.", "measure / financial", "DECIMAL(15,2)"),
]


@pytest.fixture
def resolver():
    return ColumnResolver.__new__(ColumnResolver)


@pytest.mark.parametrize("attribute", ["invoice status", "status", "invoice amount"])
def test_scores_are_identical_for_every_variant_order(resolver, attribute):
    variants = sorted(resolver._attribute_variants("Invoice", attribute))
    for c in COLUMNS:
        scores = {resolver._score_column(list(order), c, prefer_measure=False)
                  for order in permutations(variants)}
        assert len(scores) == 1, (attribute, c.name, scores)


def test_description_bonus_belongs_to_the_variant_that_earned_it(resolver):
    # One variant matches the description, another matches the name better.
    c = column("status_code", "Lifecycle state of the record.")
    forward = resolver._score_column(["lifecycle state", "status code"], c, prefer_measure=False)
    backward = resolver._score_column(["status code", "lifecycle state"], c, prefer_measure=False)
    assert forward == backward
