from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from hybrid_contracts import *

def evidence(tenant='a'):
 return EvidenceItem(evidence_id='e',tenant_id=tenant,kind='passage',content='Claim note',sources=(SourceReference(source_id='s',tenant_id=tenant,version='1',location='page 1',kind='document'),),retrieved_at=datetime.now(timezone.utc))

@pytest.mark.parametrize('key',['tenant_id','subject_id','roles','permissions'])
def test_reject_identity_injection(key):
 with pytest.raises(ValidationError): QuestionRequest(question='Claims',**{key:'forged'})

@pytest.mark.parametrize('q',['','   '])
def test_blank(q):
 with pytest.raises(ValidationError): QuestionRequest(question=q)

def test_custom_role():
 assert AccessContext(subject_id='u',tenant_id='a',policy_version='1',roles=('future_role',)).roles == ('future_role',)

@pytest.mark.parametrize('dependency',['missing','sql'])
def test_bad_dependency(dependency):
 with pytest.raises(ValidationError): RoutingPlan(steps=(RoutingStep(step_id='sql',route=Route.SQL,purpose='query',depends_on=(dependency,)),))

def test_valid_plan():
 assert len(RoutingPlan(steps=(RoutingStep(step_id='sql',route=Route.SQL,purpose='query'),RoutingStep(step_id='graph',route=Route.GRAPH,purpose='links',depends_on=('sql',)))).steps)==2

def test_cross_tenant_source():
 with pytest.raises(ValidationError): EvidenceItem(**{**evidence().model_dump(),'tenant_id':'b'})

def test_cross_tenant_result():
 with pytest.raises(ValidationError): RetrievalResult(request_id='r',tenant_id='a',route=Route.DOCUMENT,status='complete',evidence=(evidence('b'),))

@pytest.mark.parametrize('status',['empty','failed','unavailable'])
def test_no_fabricated_evidence(status):
 with pytest.raises(ValidationError): RetrievalResult(request_id='r',tenant_id='a',route=Route.DOCUMENT,status=status,evidence=(evidence(),),warnings=('Unavailable',))

def test_partial_explained():
 with pytest.raises(ValidationError): RetrievalResult(request_id='r',tenant_id='a',route=Route.DOCUMENT,status='partial')

def test_scope_validation():
 req=RetrievalRequest(request_id='r',question='Claims',access=AccessContext(subject_id='u',tenant_id='a',policy_version='1'),route=Route.DOCUMENT,deadline_at=datetime.now(timezone.utc))
 result=RetrievalResult(request_id='other',tenant_id='a',route=Route.DOCUMENT,status='complete',evidence=(evidence(),))
 with pytest.raises(ValueError): result.validate_for(req)

def test_invalid_citation():
 with pytest.raises(ValidationError): AnswerResult(request_id='r',tenant_id='a',status='answered',answer='Claim exists',evidence=(evidence(),),citations=(Citation(citation_id='c',evidence_id='e',source_id='wrong'),))

def test_roundtrip():
 a=AnswerResult(request_id='r',tenant_id='a',status='answered',answer='Claim exists',evidence=(evidence(),),citations=(Citation(citation_id='c',evidence_id='e',source_id='s'),))
 assert AnswerResult.model_validate_json(a.model_dump_json())==a

def test_money():
 assert '"123456789.12"' in Money(amount='123456789.12',currency='USD').model_dump_json()
