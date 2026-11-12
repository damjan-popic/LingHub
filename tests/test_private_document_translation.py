"""Private translation uses existing jobs/exports with frozen server identity.

These fake providers perform no HTTP or model inference.
"""
from copy import deepcopy
import io,json,zipfile
from xml.sax.saxutils import escape
import pytest
from argon2 import PasswordHasher,Type
from fastapi.testclient import TestClient
from pilot_backend.api import create_app
from pilot_backend.document_uploads import task_for
from pilot_backend.private_translation import PROVIDER,RESOURCE
from pilot_backend.security import Failure
from pilot_backend.store import digest

ORIGIN='http://127.0.0.1:18310'
PASSWORD='synthetic document test password'

def pin(label='first'):
    p={'profile_id':PROVIDER,'version':1,'artifact':{'sha256':label},
       'endpoint':'http://127.0.0.1:19999','capabilities':{'plain_text_export':True,'simple_word_export':True}}
    return {**p,'profile_sha256':digest(p)}

class Adapter:
    def __init__(self):
        self.profile=pin();self.preflights=[];self.freezes=[];self.cancellations=[];self.input_too_long=False
    def translation_profile(self):return deepcopy(self.profile)
    def translation_preflight(self,profile,texts):
        self.preflights.append((deepcopy(profile),deepcopy(texts)))
        if self.input_too_long:
            error=Failure('document_translation_input_limit',422)
            error.preflight={'status':'unsupported','profile_hash':profile['profile_sha256'],
                'sections':[{'source_sha256':x['source_sha256'],'native_input_tokens':2049,'input_limit':2048,'fits':False}for x in texts]}
            raise error
        return {'contract':'klikai.private-translation-profile.v1','status':'ready',
            'profile_hash':profile['profile_sha256'],'sections':[{'source_sha256':x['source_sha256'],
            'native_input_tokens':25,'input_limit':2048,'fits':True}for x in texts]}
    def translation_cancel(self,**request):self.cancellations.append(request);return {'status':'not_active'}
    def freeze(self,family,policy):
        self.freezes.append(deepcopy(family));return {'bundle':{'task':deepcopy(family['request_options']['document_task'])},'identity':{'test':'fake'}}
    def generate(self,bundle,tier,model,parent=None):
        t=bundle['task'];proposal='Traduzione '+str(len(self.freezes))
        return {'status':'partial','answer_md':proposal,'short_answer':proposal,'evidence':[],
            'document_result':{**{k:t[k]for k in ('mode','source_sha256','source_language','target_language','locale')},
            'proposed_text':proposal,'edits':[],'notes':[],'status':'draft','requires_human_review':True,
            'validation_scope':'translation_structure_and_protected_content_only'}}

@pytest.fixture
def rig(tmp_path):
    adapter=Adapter();app=create_app({'store':str(tmp_path/'store.sqlite'),'allowed_origins':[ORIGIN],
        'secure_cookies':False,'unified_answers':True,'document_uploads':True,'private_translation':True,
        'models':['auto'],'klikai_url':'http://127.0.0.1:18313'},adapter=adapter,start_workers=False,
        hasher=PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1,type=Type.ID))
    for name in ['one','two','three','four']:
        app.state.auth.create(name,PASSWORD,role='tester',origin='synthetic',must_change=False)
    with TestClient(app,base_url=ORIGIN)as client:yield app,client,adapter

def login(client,name='one'):
    r=client.post('/api/pilot/login',json={'username':name,'password':PASSWORD},headers={'Origin':ORIGIN})
    assert r.status_code==200;return {'Origin':ORIGIN,'X-CSRF-Token':r.json()['csrf_token']}

def upload(client,h,key='up',text='Prvi odstavek.\n\nDrugi odstavek.',fmt='docx'):
    data=text.encode()
    if fmt=='docx':
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED)as z:
            z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join('<w:p><w:r><w:t>'+escape(p)+'</w:t></w:r></w:p>'for p in text.split('\n\n'))+'</w:body></w:document>')
        data=out.getvalue()
    r=client.post('/api/pilot/documents/upload',content=data,headers={**h,
        'Content-Type':'application/octet-stream','X-Document-Name':'synthetic.'+fmt,'X-Upload-Key':'private-upload-'+key})
    assert r.status_code==200;return r.json()['document_id']

def start(client,h,id,**overrides):
    return client.post('/api/pilot/documents/'+id+'/start',headers=h,json={
        'mode':'translate','source_language':'sl','target_language':'it','locale':'sl',
        'translation_provider':PROVIDER,'idempotency_key':'start-'+id,**overrides})

def finish(app):
    for _ in range(12):
        app.state.documents.advance();job=app.state.jobs.claim()
        if job:app.state.jobs.execute(job)

def test_server_profile_is_pinned_for_every_section_and_redacted_from_browser(rig):
    app,c,a=rig;h=login(c);id=upload(c,h);r=start(c,h,id);assert r.status_code==200,r.text
    assert r.json()['options']['translation_provider']==PROVIDER
    assert 'translation_profile'not in r.json()['options']
    assert '19999'not in r.text
    original=deepcopy(a.profile);a.profile=pin('changed-config')
    # Re-delivery doesn't re-preflight or replace the frozen profile.
    assert start(c,h,id).status_code==200;assert len(a.preflights)==1
    finish(app)
    assert len(a.freezes)==2
    tasks=[f['request_options']['document_task']for f in a.freezes]
    assert all(t['translation_profile']==original and 'translation_context'not in t for t in tasks)
    assert [t['translation_request_id']for t in tasks]==['document-'+id+'-0','document-'+id+'-1']
    with app.state.store.connect()as db:
        assert {r[0]for r in db.execute('SELECT model FROM jobs')}=={RESOURCE}
    text=c.get('/api/pilot/documents/'+id+'/draft/txt').content.decode()
    assert text=='Traduzione 1\n\nTraduzione 2'
    assert 'Prvi odstavek'not in text and 'Izvirnik'not in text
    word=c.get('/api/pilot/documents/'+id+'/draft/docx');assert word.status_code==200
    with zipfile.ZipFile(io.BytesIO(word.content))as z:assert b'Traduzione 1'in z.read('word/document.xml')

@pytest.mark.parametrize('extra',[{'mode':'review','target_language':None},{'source_language':'it','target_language':'sl'},
    {'translation_provider':'other'},{'translation_profile':{'profile_id':PROVIDER}},{'origin':'http://example.org'}])
def test_browser_cannot_choose_ineligible_direction_or_author_profile(rig,extra):
    app,c,a=rig;h=login(c);id=upload(c,h)
    assert start(c,h,id,**extra).status_code in {400,422}
    assert not a.preflights and not a.freezes

def test_private_capability_is_off_by_default(rig):
    app,c,a=rig;app.state.documents.private_translation=False;h=login(c);id=upload(c,h)
    assert start(c,h,id).status_code==422;assert not a.preflights

def test_preflight_whole_document_limit_keeps_original_without_job(rig):
    app,c,a=rig;h=login(c);id=upload(c,h,text='\n\n'.join('Odstavek '+str(i)for i in range(5)))
    assert start(c,h,id).status_code==422;assert not a.preflights
    id=upload(c,h,key='another');a.input_too_long=True
    assert start(c,h,id).status_code==422
    result=c.get('/api/pilot/documents/'+id).json()
    assert result['state']=='draft'and 'document_translation_input_limit'in result['warnings']
    assert 'translation_failed_preflight'not in result['options']
    with app.state.store.connect()as db:
        saved=json.loads(db.execute('SELECT options FROM uploaded_documents WHERE id=?',(id,)).fetchone()[0])
        assert saved['translation_failed_preflight']['sections'][0]['native_input_tokens']==2049
    with app.state.store.connect()as db:assert db.execute('SELECT count(*)FROM jobs').fetchone()[0]==0

def test_inline_candidate_rejects_before_any_preflight(rig):
    app,c,a=rig;h=login(c);id=upload(c,h)
    with app.state.store.tx()as db:
        blocks=json.loads(db.execute('SELECT blocks FROM uploaded_documents WHERE id=?',(id,)).fetchone()[0]);blocks[0]['inline_spans']=[{'id':'span','kind':'emphasis','start':0,'end':4,'text':'Prvi'}]
        db.execute('UPDATE uploaded_documents SET blocks=?WHERE id=?',(json.dumps(blocks),id))
    assert start(c,h,id).status_code==422;assert not a.preflights

def test_serial_document_admission_and_queue_capacity(rig):
    app,c,a=rig;ids=[]
    for name in ['one','two','three']:
        h=login(c,name);id=upload(c,h,key=name);ids.append(id);assert start(c,h,id).status_code==200
    h=login(c,'four');id=upload(c,h,key='four');assert start(c,h,id).status_code==429
    app.state.documents.advance()
    with app.state.store.connect()as db:
        assert db.execute("SELECT count(*)FROM uploaded_documents WHERE state='running'").fetchone()[0]==1
        assert db.execute("SELECT count(*)FROM jobs WHERE state='queued'").fetchone()[0]==1
    job=app.state.jobs.claim();app.state.jobs.execute(job);app.state.documents.advance()
    assert len(a.freezes)==1
    with app.state.store.connect()as db:
        assert db.execute("SELECT count(*)FROM uploaded_documents WHERE state='running'").fetchone()[0]==1

def test_cancellation_marks_owned_request_and_late_result_cannot_complete(rig):
    app,c,a=rig;h=login(c);id=upload(c,h);assert start(c,h,id).status_code==200
    app.state.documents.advance();job=app.state.jobs.claim()
    assert c.post('/api/pilot/documents/'+id+'/cancel',headers=h,json={}).status_code==200
    assert a.cancellations[0]['translation_request_id']=='document-'+id+'-0'
    app.state.jobs.execute(job)
    assert not a.freezes
    assert c.get('/api/pilot/documents/'+id+'/draft/txt').status_code==409
    app.state.documents.advance();assert len(a.cancellations)==1

def test_queue_expiry_does_not_dispatch_or_allow_complete_export(rig,monkeypatch):
    app,c,a=rig;h=login(c);id=upload(c,h);assert start(c,h,id).status_code==200
    with app.state.store.connect()as db:
        options=json.loads(db.execute('SELECT options FROM uploaded_documents WHERE id=?',(id,)).fetchone()[0])
    import pilot_backend.document_uploads as module
    monkeypatch.setattr(module,'stamp',lambda:options['translation_queued_at']+1801)
    app.state.documents.advance()
    doc=c.get('/api/pilot/documents/'+id).json();assert doc['state']=='failed'
    assert 'document_translation_expired'in doc['warnings']and not a.freezes
    assert c.get('/api/pilot/documents/'+id+'/draft/txt').status_code==409


def test_plain_txt_uses_the_same_complete_clean_exports_without_new_generation(rig):
    app,c,a=rig;h=login(c);id=upload(c,h,fmt='txt',text='Navadno slovensko besedilo.')
    assert start(c,h,id).status_code==200;finish(app);assert len(a.freezes)==1
    assert c.get('/api/pilot/documents/'+id+'/draft/txt').content==b'Traduzione 1'
    assert c.get('/api/pilot/documents/'+id+'/draft/docx').status_code==200
    assert c.get('/api/pilot/documents/'+id+'/export/txt').status_code==200
    assert len(a.freezes)==1


def test_native_preflight_http_contract_rejects_over_budget_and_wrong_pin_without_dispatch():
    from pilot_backend.adapter import Gateway
    gateway=object.__new__(Gateway);profile=pin();texts=[{'text':'A','source_sha256':'a'*64}]
    result={'contract':'klikai.private-translation-profile.v1','profile_hash':profile['profile_sha256'],
        'status':'ready','sections':[{'source_sha256':'a'*64,'native_input_tokens':2049,'input_limit':2048,'fits':False}]}
    gateway.request=lambda *args:deepcopy(result)
    with pytest.raises(Failure,match='document_translation_input_limit'):gateway.translation_preflight(profile,texts)
    result.update(profile_hash='wrong')
    with pytest.raises(Failure,match='document_translation_preflight_failed'):gateway.translation_preflight(profile,texts)
