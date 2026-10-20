"""A COUNT whose "attribute" names the counted records means counting records.

Seen live (2026-10-19): for "How many open invoices are there?" the model
sometimes returned measure_attribute "invoices" instead of none, and the
question was refused as an attribute COUNT. Real attributes stay refused.
"""
import pytest

from semantic.normalizer import SemanticQueryNormalizer


@pytest.fixture
def normalizer():
    return SemanticQueryNormalizer.__new__(SemanticQueryNormalizer)


@pytest.mark.parametrize("attribute,concept", [
    ("invoices", "Invoice"), ("invoice", "Invoice"), ("Invoices", "Invoice"),
    ("records", "Invoice"), ("invoice records", "Invoice"),
    ("payment receipts", "PaymentReceipt"), ("receipts", "PaymentReceipt"),
    ("invoice count", "Invoice"), ("number of invoices", "Invoice"),
])
def test_naming_the_counted_records_is_a_record_count(normalizer, attribute, concept):
    assert normalizer._looks_like_count_phrase(attribute=attribute, concept=concept)


@pytest.mark.parametrize("attribute", ["status", "amount", "invoice amount", "customers", "claims"])
def test_real_attributes_and_other_records_are_not(normalizer, attribute):
    assert not normalizer._looks_like_count_phrase(attribute=attribute, concept="Invoice")
