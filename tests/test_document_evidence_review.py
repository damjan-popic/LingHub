"""Operational contracts for the new strategy, independent of writer quality."""
from copy import deepcopy
from types import SimpleNamespace
import hashlib,json
import pytest
from pilot_backend import document_transform as transform
from pilot_backend import document_files,document_summary
from pilot_backend.evidence_review import checked,VERSION,STRATEGY
from pilot_backend.document_report_sources import report_section
from pilot_backend.store import digest
from pilot_backend.security import Failure
from test_document_uploads import rig,login,upload,start,finish

TEXT='Zapis iz dokumenta.'
LOCATION='word/document.xml/body[1]/p[1]@0:18'

def sample(task=None):
    task=task or {'mode':'review','source_language':'sl','target_language':None,'locale':'sl',
        'source_text':TEXT,'source_sha256':hashlib.sha256(TEXT.encode()).hexdigest(),'review_strategy':STRATEGY,
        'document_binding':{'document_id':'offline-test','section_index':0},'document_revision':'0'*64,'document_location':LOCATION}
    text=task['source_text'];source={'evidence_id':'E1','source_slug':'recorded-source','external_id':'R1',
        'capture_sha256':'1'*64,'source_record_revision':'2'*64,'title':'Recorded source','url':None,
        'rights_mode':'restricted','locator':{},'qualifications':{},'focus':{'char_start':0,'char_end':6,'text':'Zapis.','text_sha256':hashlib.sha256(b'Zapis.').hexdigest()},
        'recorded_advice':None,'untyped_context_limit':'Literal context only.'}
    finding={'finding_id':'F'+'a'*24,'type':'recorded_form_context','applicability':'no_error_asserted',
        'match':{'observed':{'start':0,'end':len(text),'text':text}},'source':source,
        'related_source_captures':[{'evidence_id':'E1','capture_sha256':'1'*64}],
        'document_revision':task['document_revision'],'document_location':task['document_location'],
        'proposed_edit':None,'included_in_draft':False,'human_acceptance':'not_recorded','notice':'Recorded context; no error asserted.'}
    review={'version':VERSION,'strategy':STRATEGY,'source_sha256':task['source_sha256'],
        'document_binding':task['document_binding'],'document_revision':task['document_revision'],'document_location':task['document_location'],
        'findings':[finding],'checks':[{'check_id':'captured_source_forms','status':'completed','scope':'Bounded literal source forms.'}],
        'overview':{'counts':{'qualified_advice':0,'recorded_contrast':0,'recorded_form_context':1,'executable_application':0},
            'included_edits':0,'processing':'completed'},'applications':{'status':'not_needed','edits':[],'applications':[]},
        'generation':{'writer_invoked':False,'generative_attempts':0},'source_capture_set_sha256':'3'*64}
    review['manifest_sha256']=digest(review)
    doc={key:task[key]for key in ('mode','source_language','target_language','locale','source_sha256')}
    doc.update(status='draft',proposed_text=text,edits=[],notes=[],requires_human_review=True,
        validation_scope='evidence_review_existing_applications',review=review)
    row={'evidence_id':'E1','capture_sha256':'1'*64,'context_eligible':True,'capture':{'original_text':'Zapis.','source_revision':'2'*64},
        'source_slug':'recorded-source','external_id':'R1','source':{'source_id':'recorded-source:R1'}}
    return task,doc,{'E1':row}


def test_new_result_validates_locations_source_and_has_no_edit_authority():
    task,doc,rows=sample()
    assert transform.validate_document_result(doc,task,rows)==doc
    for change in ('source','revision','edit'):
        changed=deepcopy(doc);review=changed['review']
        if change=='source':review['findings'][0]['source']['focus']['text']='Other.'
        elif change=='revision':review['document_revision']='4'*64
        else:review['applications']['edits']=[{'start':0,'end':1}]
        review['manifest_sha256']=digest({k:v for k,v in review.items()if k!='manifest_sha256'})
        with pytest.raises(Failure):transform.validate_document_result(changed,task,rows)


def test_writer_free_generation_bypasses_legacy_and_keeps_requested_failure(monkeypatch):
    task,doc,rows=sample();doc['review']['checks'].append({'check_id':'comma_prediction','status':'failed','scope':'Dedicated checker.'})
    doc['review']['manifest_sha256']=digest({k:v for k,v in doc['review'].items()if k!='manifest_sha256'})
    monkeypatch.setattr(transform,'checked',lambda bundle:None)
    monkeypatch.setattr(transform,'_mixed_document_answer',lambda *a:pytest.fail('Legacy writer route entered'))
    response={'contract':transform.RESPONSE_CONTRACT,'status':'ok','document_result':doc,'evidence':[{'evidence_id':'E1'}],
        'execution':{'inference_performed':False},'provider_attempts':[]}
    calls=[]
    def request(path,payload):calls.append((path,payload));return response
    bundle={'document_task':task,'source_bundle':{'matches':list(rows.values())},'unified_evidence':{},'retrieval':{}}
    result=transform._generate_evidence(SimpleNamespace(request=request),bundle,'short','cpu')
    assert len(calls)==1 and calls[0][1]['review_strategy']==STRATEGY
    assert result['document_result']['proposed_text']==task['source_text']
    assert result['limitations']==[{'code':'document_transform_failed','scope':'answer','operation':'comma_prediction'}]
    assert document_files.failed_document_operation(result)


def test_reports_call_finding_sources_evidence_not_unknown_writer_input():
    task,doc,rows=sample()
    section={'text':TEXT,'document_result':doc,'family_id':'family-test','sources':[{'evidence_id':'E1','title':'Recorded source'}]}
    rendered=report_section(section,{'source_bundle':{'matches':list(rows.values())}},{},'/api/pilot')
    assert rendered['writer_input_selection']=='not_invoked'
    assert rendered['sources'][0]['report_role']=='finding_evidence'
    assert rendered['sources'][0]['frozen_source_url'].endswith('/sources/E1?tier=answer')
    body=document_files.report_text({'locale':'sl','mode':'review','sections':[{**rendered,'index':0,'state':'complete','location':LOCATION}]}).decode()
    assert 'Recorded context' in body and 'modela ni znan'not in body


def test_ordinary_start_uses_file_revision_cpu_job_and_existing_cancel(rig):
    app,client,adapter,ids=rig;app.state.documents.evidence_review=True
    headers=login(client);doc=upload(client,headers)
    response=start(client,headers,doc['document_id']);assert response.status_code==200
    assert response.json()['options']['review_strategy']==STRATEGY
    app.state.documents.advance()
    with app.state.store.connect()as db:
        options=db.execute('SELECT options FROM family_request_options').fetchone()[0]
        job=db.execute('SELECT model FROM jobs ORDER BY created DESC LIMIT 1').fetchone()
    task=json.loads(options)['document_task']
    assert task['document_revision']==doc['source_sha256']and task['document_location'].startswith('text@')
    assert job['model']=='cpu'
    cancelled=client.post('/api/pilot/documents/'+doc['document_id']+'/cancel',headers=headers,json={})
    assert cancelled.status_code==200 and cancelled.json()['state']=='cancelled'


def test_legacy_saved_jobs_and_other_language_retain_original_strategy(rig):
    app,client,adapter,ids=rig;headers=login(client);doc=upload(client,headers)
    assert start(client,headers,doc['document_id']).status_code==200
    app.state.documents.advance()
    with app.state.store.connect()as db:options=db.execute('SELECT options FROM family_request_options').fetchone()[0]
    assert 'review_strategy'not in json.loads(options)['document_task']


def test_summary_is_separate_and_unknown_ids_cannot_replace_server_counts():
    task,doc,rows=sample()
    document={'document_id':'offline-test','source_sha256':'0'*64,'state':'complete',
        'options':{'review_strategy':STRATEGY,'locale':'sl'},'sections':[{'index':0,'location':LOCATION,
        'state':'complete','text':TEXT,'document_result':doc,'limitations':[]}]}
    before=document_summary.manifest(document)
    packet=document_summary.packet(document,[{'source_bundle':{'matches':list(rows.values())}}])
    document['options']['editorial_summary']={'status':'unavailable','output':None}
    assert document_summary.manifest(document)==before
    assert packet['server_overview']==before
    assert packet['sources'][next(iter(packet['sources']))]['original_text']=='Zapis.'
    summary=document_summary.public_summary({'status':'available','manifest_sha256':'9'*64,'output':{'points':[]}},before)
    assert summary['historical']is True


def test_report_reuses_only_identical_complete_advice_and_keeps_occurrence_identity():
    from pilot_backend.evidence_review import report_blocks
    task,doc,rows=sample();review=deepcopy(doc['review']);first=review['findings'][0]
    first['notice']='Zapis. Governing condition A; alternative B remains available.'
    first['document_revision']='a'*64
    second=deepcopy(first);second['finding_id']='F'+'b'*24
    second['match']['observed']={'start':10,'end':15,'text':'Other'}
    review['findings'].append(second)
    before=deepcopy(review);blocks=list(report_blocks(review));text='\n'.join(value for kind,value in blocks)
    assert text.count(first['notice'])==1
    assert 'Ugotovitev 1'in text and 'Ugotovitev 2'in text and '10–15: Other'in text
    assert 'pri ugotovitvi 1'in text
    assert not any(kind=='quote'and value==first['source']['focus']['text']for kind,value in blocks)
    assert first['finding_id']not in text and first['document_revision']not in text
    assert review==before
    # Distinct source ownership must never disappear behind an identical quote.
    second['source']['capture_sha256']='9'*64
    text='\n'.join(value for kind,value in report_blocks(review))
    assert text.count(first['notice'])==2
