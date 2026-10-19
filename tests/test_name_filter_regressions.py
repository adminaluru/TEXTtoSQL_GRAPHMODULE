"""Offline regressions: names are literal substrings, never wildcard patterns."""
import pytest
from ontology.models import ColumnMetadata
from planning.column_resolver import ColumnResolver

COLUMN = ColumnMetadata(key='customer.name', name='name', qualified_name='public.customer.name', table_key='public.customer', table_name='customer', data_type='text')

@pytest.mark.parametrize('value', ['', ' ', '\t\n'])
def test_blank_name_rejected(value):
    with pytest.raises(ValueError, match='blank'):
        ColumnResolver._resolve_filter_match('customer name', COLUMN, value)

@pytest.mark.parametrize('value,expected', [
    ('Blue Prism', '%Blue Prism%'),
    ('  Blue Prism  ', '%Blue Prism%'),
    ('100%', r'%100\%%'),
    ('A_B', r'%A\_B%'),
    (r'A\B', r'%A\\B%'),
    (r'A\_%', r'%A\\\_\%%'),
    ("O'Brien", "%O'Brien%"),
])
def test_name_is_literal_substring(value, expected):
    assert ColumnResolver._resolve_filter_match('customer name', COLUMN, value) == ('ILIKE', expected)

@pytest.mark.parametrize('value', ['PAID', 'INV_001%', '', 123, None])
def test_other_filters_keep_exact_semantics(value):
    column = COLUMN.model_copy(update={'name':'status'})
    assert ColumnResolver._resolve_filter_match('status', column, value) == ('=', value)
