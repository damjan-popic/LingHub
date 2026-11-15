"""Synthetic end-to-end state/decision/export controls; no model/network."""
from copy import deepcopy
import json
import pytest
from pilot_backend import document_ai_suggestions as ai
from pilot_backend.document_ai_contract import VERSION,AI_LABEL,EVIDENCE_LABEL,checked_output,digest
from pilot_backend.document_uploads import Documents
from test_document_uploads import rig,login,upload,start,finish
from test_document_evidence_review import sample

class SyntheticGateway:
    def __init__(self,unavailable=False):self.calls=[];self.unavailable=unavailable
    def request(self,path,payload):
        self.calls.append(path);packet=payload['packet']
        if path.endswith('/prepare'):
            value={'version':VERSION,'packet_sha256':packet['packet_sha256'],'status':'prepared',
                   'purpose':'SYNTHETIC-TEST-ONLY','request_sha256':'0'*64}
            value['preparation_sha256']=digest(value);return value
        assert path=='/internal/pilot/document-ai-suggestions'
        text=packet['sections'][0]['evidence_draft'];a=text.index('je je')
        item={'kind':'additional_suggestion','section_index':0,'start':a,'end':a+5,'observed_text':'je je',
              'replacement':'je','explanation':'Synthetic mechanical fixture, not an actual AI language outcome.',
              'uncertainty':'','finding_ids':[],'source_refs':[]}
        raw={'items':[item,deepcopy(item)]}
        result={'version':VERSION,'packet_sha256':packet['packet_sha256'],'evidence_manifest_sha256':packet['evidence_manifest_sha256'],
            'preparation_sha256':payload['prepared']['preparation_sha256'],
            'origin':'ai_generated','authority':'not_source_authorized','status':'unavailable'if self.unavailable else'available',
            'attempted':False,'inference_performed':False,'retries':0,'items':[]if self.unavailable else checked_output(raw,packet),
            'raw_content':json.dumps(raw),'coverage':packet['coverage']}
        result['result_sha256']=digest(result);return result

def setup(rig,unavailable=False,word=False):
    app,client,adapter,ids=rig;documents=app.state.documents;documents.evidence_review=True;documents.ai_suggestions=True
    gateway=SyntheticGateway(unavailable);documents.domain.request_gateway=gateway
    def freeze(family,policy):
        if family.get('request_options',{}).get('document_ai'):return ai.freeze(gateway,family,policy)
        task=family['request_options']['document_task'];task,doc,rows=sample(task)
        return {'bundle':{'task':task,'result':doc,'source_bundle':{'matches':list(rows.values())}},'identity':{'fixture':'SYNTHETIC-ONLY'}}
    def generate(bundle,tier,model,parent=None):
        if bundle.get('contract')==ai.CONTRACT:return ai.generate(gateway,bundle,tier,model,parent)
        return {'status':'source_only','answer_md':'Synthetic evidence fixture.','short_answer':'Synthetic evidence fixture.',
                'document_result':bundle['result'],'evidence':[],'source_badges':[],'limitations':[]}
    adapter.freeze=freeze;adapter.generate=generate
    headers=login(client)
    if word:
        from test_document_word import package
        data=package('<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Ana</w:t></w:r><w:r><w:t xml:space="preserve"> je je doma.</w:t></w:r></w:p>',{'word/media/unchanged.bin':b'SYNTHETIC-STYLE-CONTROL'})
        response=client.post('/api/pilot/documents/upload',content=data,headers={**headers,'Content-Type':'application/octet-stream','X-Document-Name':'test.docx','X-Upload-Key':'ai-synthetic-word'})
        assert response.status_code==200;document=response.json()
    else:document=upload(client,headers,b'Ana je je doma.',key='ai-synthetic-doc')
    identifier=document['document_id'];assert start(client,headers,identifier).status_code==200;finish(app)
    evidence=client.get('/api/pilot/documents/'+identifier).json();assert evidence['state']=='complete'
    return app,client,documents,gateway,headers,identifier,evidence

def generate_ai(rig,unavailable=False,word=False):
    app,client,documents,gateway,headers,identifier,evidence=setup(rig,unavailable,word)
    root='/api/pilot/documents/'+identifier
    prepared=client.post(root+'/ai/prepare',headers=headers,json={});assert prepared.status_code==200,prepared.text
    assert prepared.json()['ai_suggestions']['state']=='prepared'
    queued=client.post(root+'/ai/start',headers=headers,json={});assert queued.status_code==200,queued.text
    duplicate=client.post(root+'/ai/start',headers=headers,json={});assert duplicate.json()['ai_suggestions']['family_id']==queued.json()['ai_suggestions']['family_id']
    finish(app);after=client.get(root).json()
    assert after['review_manifest']==evidence['review_manifest']
    assert after['sections']==evidence['sections']
    return app,client,documents,gateway,headers,identifier,evidence,after

def decide(client,headers,identifier,ai_state,item,choice):
    return client.post('/api/pilot/documents/'+identifier+'/ai/decision',headers=headers,json={
        'suggestion_id':item['suggestion_id'],'decision':choice,'result_sha256':ai_state['result_sha256'],
        'evidence_manifest_sha256':ai_state['evidence_manifest_sha256']})

def test_saved_decisions_separate_exports_overlap_undo_and_ownership(rig):
    app,client,documents,gateway,headers,identifier,evidence,after=generate_ai(rig)
    state=after['ai_suggestions'];assert state['state']=='available'and state['decisions']=={}and not state['selected_revision']
    first,second=state['items'];assert first['eligible_for_accept']
    accepted=decide(client,headers,identifier,state,first,'accept');assert accepted.status_code==200,accepted.text
    selected=accepted.json()['ai_suggestions']['selected_revision'];revision=selected['revision_id']
    assert selected['origin']=='user_selected_unverified_ai'and not selected['acceptance_is_certification']
    assert selected['sections'][0]['document_result']['proposed_text']=='Ana je doma.'
    assert decide(client,headers,identifier,state,second,'accept').status_code==409
    response=client.get('/api/pilot/documents/'+identifier+'/selected/txt?revision='+revision)
    assert response.status_code==200 and response.content==b'Ana je doma.'
    assert 'user-selected-AI'in response.headers['content-disposition']
    base=client.get('/api/pilot/documents/'+identifier+'/draft/txt');assert base.content==b'Ana je je doma.'
    report=client.get('/api/pilot/documents/'+identifier+'/export/txt')
    assert report.status_code==200
    assert report.text.index(EVIDENCE_LABEL)<report.text.index(AI_LABEL)
    assert 'Sprejel uporabnik'in report.text and 'Sprejem ni jezikovno potrdilo.'in report.text
    # Query a fresh Documents object over the same durable store.
    with documents.store.connect()as db:owner=db.execute('SELECT owner FROM uploaded_documents WHERE id=?',(identifier,)).fetchone()[0]
    reopened=Documents(documents.store,documents.domain,evidence_review=True,ai_suggestions=True)
    assert reopened.get(owner,identifier)['ai_suggestions']['selected_revision']['revision_id']==revision
    undone=decide(client,headers,identifier,state,first,'undecided');assert undone.status_code==200
    assert undone.json()['ai_suggestions']['selected_revision']is None
    assert client.get('/api/pilot/documents/'+identifier+'/selected/txt?revision='+revision).status_code==409
    dismissed=decide(client,headers,identifier,state,second,'dismiss');assert dismissed.status_code==200
    assert dismissed.json()['review_manifest']==evidence['review_manifest']
    assert gateway.calls.count('/internal/pilot/document-ai-suggestions')==1
    other=login(client,'two')
    assert client.post('/api/pilot/documents/'+identifier+'/ai/start',headers=other,json={}).status_code==404

def test_ai_unavailable_keeps_evidence_and_no_retry_action(rig):
    app,client,documents,gateway,headers,identifier,evidence,after=generate_ai(rig,True)
    assert after['ai_suggestions']['state']=='unavailable'and after['state']=='complete'
    assert client.get('/api/pilot/documents/'+identifier+'/draft/txt').content==b'Ana je je doma.'
    client.post('/api/pilot/documents/'+identifier+'/ai/start',headers=headers,json={});finish(app)
    assert gateway.calls.count('/internal/pilot/document-ai-suggestions')==1

def test_ai_cancellation_does_not_cancel_completed_evidence(rig):
    app,client,documents,gateway,headers,identifier,evidence=setup(rig)
    root='/api/pilot/documents/'+identifier
    assert client.post(root+'/ai/prepare',headers=headers,json={}).status_code==200
    assert client.post(root+'/ai/start',headers=headers,json={}).status_code==200
    cancelled=client.post(root+'/ai/cancel',headers=headers,json={});assert cancelled.status_code==200
    assert cancelled.json()['state']=='complete'and cancelled.json()['ai_suggestions']['state']=='cancelled'
    finish(app);assert gateway.calls==['/internal/pilot/document-ai-suggestions/prepare']
    assert client.get(root).json()['review_manifest']==evidence['review_manifest']


def test_selected_word_keeps_native_styles_parts_and_no_ai_warning_in_body(rig):
    import io
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    from pilot_backend import document_files as files
    app,client,documents,gateway,headers,identifier,evidence,after=generate_ai(rig,word=True)
    original=client.get('/api/pilot/documents/'+identifier+'/draft/docx').content
    state=after['ai_suggestions'];item=state['items'][0]
    accepted=decide(client,headers,identifier,state,item,'accept');assert accepted.status_code==200
    revision=accepted.json()['ai_suggestions']['selected_revision']['revision_id']
    response=client.get('/api/pilot/documents/'+identifier+'/selected/docx?revision='+revision)
    assert response.status_code==200
    selected=response.content
    with ZipFile(io.BytesIO(original))as before,ZipFile(io.BytesIO(selected))as changed:
        assert before.namelist()==changed.namelist()
        assert changed.read('word/media/unchanged.bin')==before.read('word/media/unchanged.bin')
        root=ET.fromstring(changed.read('word/document.xml'))
        assert root.find('.//'+files.W+'rPr/'+files.W+'b')is not None
        text=''.join(t.text or''for t in root.iter(files.W+'t'))
        assert text=='Ana je doma.'and AI_LABEL not in text
    assert client.get('/api/pilot/documents/'+identifier+'/draft/docx').content==original

def test_future_coordinate_labels_keep_length_changing_historical_evidence_exact():
    from pilot_backend.document_ai_contract import PACKET,sha
    original='Nadaljni opis.';draft='Nadaljnji opis.'
    edit={'start':0,'end':8,'original':'Nadaljni','replacement':'Nadaljnji'}
    finding={'finding_id':'F'+'a'*24,'match':{'observed':{'start':0,'end':8,'text':'Nadaljni'}},
       'proposed_edit':deepcopy(edit),'alternatives':[],'included_in_draft':True,
       'applicability':'source_recommendation_applied','source_refs':['S'+'b'*16],
       'source_focus':{'char_start':4,'char_end':12,'text':'Nadaljni'}}
    section={'index':0,'location':'text@0:14','original_text':original,'original_sha256':sha(original),
       'evidence_draft':draft,'draft_sha256':sha(draft),'evidence_edits':[edit],'findings':[finding],
       'coordinate_guide':[{'start':10,'end':14,'text':'opis'}]}
    value={'version':PACKET,'coordinate_base':'frozen_evidence_draft_unicode_codepoint_half_open',
       'document_revision':'0'*64,'evidence_manifest_sha256':'1'*64,'locale':'sl','sections':[section],
       'sources':{'S'+'b'*16:{'original_text':'abc Nadaljni','body_sha256':sha('abc Nadaljni')}},
       'coverage':{'provided_sections':[0],'omitted_sections':[]}}
    before=deepcopy(value);ai.label_coordinates(value);value['packet_sha256']=digest(value)
    assert value['coordinate_base_scope']=='new_ai_items_only'
    spaces=value['coordinate_spaces'];assert 'sections[].evidence_edits'in spaces['original_text_unicode_codepoint_half_open']
    assert 'sections[].findings[].proposed_edit'in spaces['original_text_unicode_codepoint_half_open']
    assert 'sections[].coordinate_guide'in spaces['evidence_draft_unicode_codepoint_half_open']
    assert 'sections[].findings[].source_focus'in spaces['bound_source_original_text_unicode_codepoint_half_open']
    assert finding['evidence_draft_relation']=='already_incorporated_historical_evidence'
    assert {k:finding[k]for k in before['sections'][0]['findings'][0]}==before['sections'][0]['findings'][0]
    assert value['sources']==before['sources']and section['evidence_edits']==before['sections'][0]['evidence_edits']
    # The following unchanged word moves by one codepoint in the draft. New
    # AI output still uses exact draft offsets and maps back to original 9:13.
    row={'kind':'additional_suggestion','section_index':0,'start':10,'end':14,'observed_text':'opis',
       'replacement':'zapis','explanation':'Synthetic mechanical coordinate fixture only.',
       'uncertainty':'','source_refs':[],'finding_ids':[]}
    item,=checked_output({'items':[row]},value)
    assert item['eligible_for_accept']and item['original_span']=={'start':9,'end':13}
    assert section['original_text'][9:13]==section['evidence_draft'][10:14]=='opis'
