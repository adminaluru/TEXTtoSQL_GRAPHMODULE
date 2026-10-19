"""Offline compiler regressions: never silently remove existence predicates."""
import pytest
from planning.models import QueryExistence, QueryJoin
from sql_compiler.compiler import DeterministicSQLCompiler

A='public.customer'
B='public.invoice'
C='public.claim'

def edge(left, lc, right, rc, kind='INNER'):
    return QueryJoin(left_table=left,left_column=lc,right_table=right,right_column=rc,join_type=kind)

def compile_path(joins,tables=None):
    return DeterministicSQLCompiler()._compile_existence_condition(
        QueryExistence(anchor_table=A,tables=tables or [A,B,C],joins=joins),
        {A:'t0'},0)

@pytest.mark.parametrize('kind',['INNER','LEFT'])
@pytest.mark.parametrize('reverse',[False,True])
def test_additional_inner_edge_rejected(kind,reverse):
    correlation=edge(A,'id',B,'customer_id')
    first=edge(B,'id',C,'invoice_id')
    extra=edge(B,'company_id',C,'company_id',kind)
    inner=[extra,first] if reverse else [first,extra]
    with pytest.raises(ValueError,match='additional predicate'):
        compile_path([correlation,*inner])

def test_duplicate_inner_edge_is_explicitly_rejected():
    join=edge(B,'id',C,'invoice_id')
    with pytest.raises(ValueError,match='additional predicate'):
        compile_path([edge(A,'id',B,'customer_id'),join,join.model_copy()])

def test_supported_multi_hop_preserves_predicates():
    sql,params=compile_path([edge(A,'id',B,'customer_id'),edge(B,'id',C,'invoice_id')])
    assert 'e0_0.id = e0_1.invoice_id' in sql
    assert 't0.id = e0_0.customer_id' in sql
    assert params==[]

def test_multiple_anchor_conditions_already_fail_closed():
    with pytest.raises(ValueError,match='correlation join'):
        compile_path([edge(A,'id',B,'customer_id'),edge(A,'company_id',B,'company_id')], [A,B])
