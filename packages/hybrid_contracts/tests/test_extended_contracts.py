from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from pydantic import ValidationError
from hybrid_contracts import *

NOW=datetime.now(timezone.utc)

def table(value=1, kind='integer'):
 return ResultTable(table_id='t',title='Invoices',columns=(TableColumn(key='amount',label='Amount',data_type=kind),),rows=(TableRow(cells=(TableCell(value=value),),evidence_ids=('e',)),))

@pytest.mark.parametrize('value,kind',[(True,'integer'),('1','integer'),(1,'decimal'),(None,'integer')])
def test_invalid_cell_type(value,kind):
 with pytest.raises(ValidationError): table(value,kind)

def test_decimal_roundtrip():
 t=table(Decimal('123456789.12'),'decimal')
 assert ResultTable.model_validate_json(t.model_dump_json())==t

def test_date_roundtrip():
 t=table(NOW.date(),'date')
 assert ResultTable.model_validate_json(t.model_dump_json())==t

def test_row_width():
 with pytest.raises(ValidationError): ResultTable(table_id='t',title='x',columns=(TableColumn(key='x',label='x',data_type='text'),),rows=(TableRow(cells=(),evidence_ids=('e',)),))

def test_cursor_consistency():
 with pytest.raises(ValidationError): ResultTable(table_id='t',title='x',columns=(TableColumn(key='x',label='x',data_type='text'),),next_cursor='opaque')

@pytest.mark.parametrize('kwargs',[{}, {'option_id':'o','text':'both'}, {'text':' '}])
def test_clarification_requires_one_response(kwargs):
 with pytest.raises(ValidationError): ClarificationResponse(clarification_id='c',**kwargs)

def test_clarification_public_rejects_tenant():
 with pytest.raises(ValidationError): ClarificationResponse(clarification_id='c',option_id='o',tenant_id='b')

def test_clarification_prompt_requires_input():
 with pytest.raises(ValidationError): ClarificationPrompt(clarification_id='c',question='Which?',reason='Ambiguous',expires_at=NOW)

def test_prompt_options():
 assert ClarificationPrompt(clarification_id='c',question='Which?',reason='Ambiguous',options=(ClarificationOption(option_id='o',label='Acme'),),expires_at=NOW).options

def test_retry_hint():
 with pytest.raises(ValidationError): ErrorResponse(request_id='r',code='forbidden',message='Denied',retry_after_seconds=3)

@pytest.mark.parametrize('filename,kind',[('a.pdf','pdf'),('a.docx','docx'),('a.xlsx','xlsx')])
def test_supported_uploads(filename,kind):
 assert UploadMetadata(filename=filename,file_type=kind,size_bytes=2).size_bytes==2

@pytest.mark.parametrize('filename,kind',[('../a.pdf','pdf'),('a.exe','pdf'),('a.xls','xlsx')])
def test_invalid_uploads(filename,kind):
 with pytest.raises(ValidationError): UploadMetadata(filename=filename,file_type=kind,size_bytes=2)

def job(state=DocumentState.QUEUED,**kw):
 return DocumentJob(job_id='j',tenant_id='a',source_id='s',source_version='1',state=state,created_at=NOW,updated_at=NOW,**kw)

def test_ready_requires_completion():
 with pytest.raises(ValidationError): job(DocumentState.READY)

def test_failed_requires_message():
 with pytest.raises(ValidationError): job(DocumentState.FAILED)

def test_job_transition():
 assert job().can_transition_to(DocumentState.PROCESSING)
 assert not job().can_transition_to(DocumentState.READY)
 assert not job(DocumentState.DELETED).can_transition_to(DocumentState.QUEUED)

def test_unknown_table_evidence():
 from test_contracts import evidence
 with pytest.raises(ValidationError): AnswerResult(request_id='r',tenant_id='a',status='answered',answer='Invoices',evidence=(evidence(),),tables=(table().model_copy(update={'rows': (TableRow(cells=(TableCell(value=1),),evidence_ids=('missing',)),)}),))
