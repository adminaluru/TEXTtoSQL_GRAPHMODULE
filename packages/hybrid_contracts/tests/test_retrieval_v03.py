"""0.3.0: retrieval statuses, tables, clarification and the disabled retriever."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hybrid_contracts import (AccessContext, ClarificationOption, ClarificationPrompt,
                              ClarificationResponse, DisabledRetriever, EvidenceItem,
                              ResultTable, RetrievalRequest, RetrievalResult, Route,
                              SourceReference, TableCell, TableColumn, TableRow)

NOW = datetime.now(timezone.utc)


def evidence(eid="e1", tenant="a"):
    return EvidenceItem(evidence_id=eid, tenant_id=tenant, kind="structured_fact", content="Total 4000.00 USD",
                        sources=(SourceReference(source_id="s", tenant_id=tenant, version="1",
                                                 location="database", kind="database"),),
                        retrieved_at=NOW)


def table(evidence_ids=("e1",), table_id="t1"):
    return ResultTable(table_id=table_id, title="Totals",
                       columns=(TableColumn(key="v", label="Value", data_type="decimal"),),
                       rows=(TableRow(cells=(TableCell(value=Decimal("1.00")),), evidence_ids=evidence_ids),))


def prompt():
    return ClarificationPrompt(clarification_id="c", question="Which?", reason="ambiguous",
                               options=(ClarificationOption(option_id="o", label="A"),),
                               expires_at=NOW + timedelta(minutes=10))


def result(status, **kwargs):
    return RetrievalResult(request_id="r", tenant_id="a", route=Route.SQL, status=status, **kwargs)


def request(**kwargs):
    return RetrievalRequest(request_id="r", question="How much?", route=Route.SQL, deadline_at=NOW,
                            access=AccessContext(subject_id="u", tenant_id="a", policy_version="1"),
                            **kwargs)


@pytest.mark.parametrize("status", ["clarification", "unsupported", "denied", "disabled",
                                    "unavailable", "failed"])
def test_non_answer_statuses_need_an_explanation_and_carry_no_content(status):
    assert result(status, warnings=("Why",)).status == status
    with pytest.raises(ValidationError):
        result(status)
    with pytest.raises(ValidationError):
        result(status, warnings=("Why",), evidence=(evidence(),))
    with pytest.raises(ValidationError):
        result(status, warnings=("Why",), tables=(table(),))


def test_empty_needs_no_explanation_but_carries_nothing():
    assert result("empty").status == "empty"
    with pytest.raises(ValidationError):
        result("empty", tables=(table(),))


def test_tables_must_cite_included_evidence():
    assert result("complete", evidence=(evidence(),), tables=(table(),)).tables
    with pytest.raises(ValidationError, match="missing evidence"):
        result("complete", evidence=(evidence(),), tables=(table(evidence_ids=("other",)),))


def test_duplicate_table_ids_are_rejected():
    with pytest.raises(ValidationError, match="Duplicate table"):
        result("complete", evidence=(evidence(),), tables=(table(), table()))


def test_clarification_payload_only_with_clarification_status():
    assert result("clarification", warnings=("Which one?",), clarification=prompt()).clarification
    with pytest.raises(ValidationError, match="clarification status"):
        result("unsupported", warnings=("No",), clarification=prompt())


def test_request_carries_conversation_and_clarification_reply():
    req = request(conversation_id="c1",
                  clarification_response=ClarificationResponse(clarification_id="c", option_id="o"))
    assert req.conversation_id == "c1" and req.clarification_response.option_id == "o"


def test_request_without_new_fields_is_unchanged():
    req = request()
    assert req.conversation_id is None and req.clarification_response is None


def test_disabled_retriever_is_truthful():
    req = request()
    out = DisabledRetriever(Route.SQL, "Text-to-SQL is switched off here.").retrieve(req)
    out.validate_for(req)
    assert out.status == "disabled" and not out.evidence
    assert out.warnings == ("Text-to-SQL is switched off here.",)


def test_disabled_retriever_rejects_other_routes_and_blank_reasons():
    with pytest.raises(ValueError, match="wrong retriever"):
        DisabledRetriever(Route.DOCUMENT).retrieve(request())
    with pytest.raises(ValueError):
        DisabledRetriever(Route.SQL, " ")


def test_result_round_trips_through_json_with_types():
    original = result("complete", evidence=(evidence(),), tables=(table(),))
    restored = RetrievalResult.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.tables[0].rows[0].cells[0].value == Decimal("1.00")
