"""A3.9: ontology lookups that must find one entry refuse when they find several.

Before, each lookup used LIMIT 1 and silently took whichever match Neo4j
returned first, so a duplicated concept or relationship could answer from an
arbitrary table. Now a second match is detected and the question is refused
without repair (a retry could pick a different table).
"""
from unittest.mock import Mock

import pytest

from nlu.models import QuestionAnalysis
from ontology.models import OntologyAmbiguityError
from ontology.ontology_service import OntologyService
from planning.errors import UnsupportedPredicateError
from planning.graph_planner import ONTOLOGY_AMBIGUOUS, GraphPlanner


def table(key, concept="Customer"):
    return {"key": key, "table_name": key.split(".")[1], "business_concept": concept, "business_domain": None}


def relationship(kind):
    return {"source_table_key": "public.customers", "source_concept": "Customer", "relationship_type": kind,
            "target_table_key": "public.payments", "target_concept": "PaymentReceipt",
            "join_condition": None, "cardinality": None, "validation_status": "confirmed"}


def service(*results):
    neo4j = Mock()
    neo4j.execute_read.side_effect = list(results)
    return OntologyService(neo4j), neo4j


def test_unique_concept_resolves_and_probes_for_a_second_match():
    ontology, neo4j = service([table("public.customers")])
    assert ontology.resolve_table("Customer").key == "public.customers"
    assert "LIMIT 2" in neo4j.execute_read.call_args.args[0]


def test_concept_on_several_tables_is_ambiguous():
    ontology, _ = service([table("public.customers"), table("public.customers_old")])
    with pytest.raises(OntologyAmbiguityError, match="several tables"):
        ontology.resolve_table("Customer")


def test_unknown_concept_is_still_none():
    ontology, _ = service([])
    assert ontology.resolve_table("Unknown") is None


def test_several_relationships_between_concepts_are_ambiguous():
    ontology, _ = service([table("public.customers")], [table("public.payments", "PaymentReceipt")],
                          [relationship("MAKES_PAYMENT"), relationship("REFUNDS_PAYMENT")])
    with pytest.raises(OntologyAmbiguityError, match="Several business relationships"):
        ontology.find_semantic_relationship("Customer", "PaymentReceipt")


def test_several_relationships_between_tables_are_ambiguous():
    ontology, _ = service([relationship("MAKES_PAYMENT"), relationship("REFUNDS_PAYMENT")])
    with pytest.raises(OntologyAmbiguityError):
        ontology.find_semantic_relationship_between_tables("public.customers", "public.payments")


def test_single_relationship_is_returned():
    ontology, neo4j = service([relationship("MAKES_PAYMENT")])
    found = ontology.find_semantic_relationship_between_tables("public.customers", "public.payments")
    assert found.relationship_type == "MAKES_PAYMENT"
    assert "LIMIT 2" in neo4j.execute_read.call_args.args[0]


def test_planner_refuses_with_a_user_message_and_no_physical_names():
    ontology = Mock()
    ontology.resolve_table.side_effect = OntologyAmbiguityError(
        "Concept 'Customer' maps to several tables: ['public.customers', 'public.customers_old']")
    analysis = QuestionAnalysis(intent="aggregate", concepts=["Customer"], aggregation="COUNT")
    with pytest.raises(UnsupportedPredicateError) as caught:
        GraphPlanner(ontology).plan(analysis)
    assert str(caught.value) == ONTOLOGY_AMBIGUOUS
    assert "public." not in str(caught.value)
