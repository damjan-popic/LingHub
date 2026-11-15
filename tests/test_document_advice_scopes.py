"""Offline ordinary API/queue controls with synthetic gateway; no services."""
from copy import deepcopy
import json,io
from zipfile import ZipFile
import pytest
from pilot_backend import document_ai_suggestions as ai
from pilot_backend.document_ai_contract import VERSION,checked_output,digest
from test_document_uploads import rig,start,finish
from test_document_ai_suggestions import setup,decide


class Interrupted(BaseException):pass


class ScopedGateway:
    def __init__(self,modes=None,interrupt=False):self.modes=modes or{};self.calls=[];self.interrupt=interrupt
    def request(self,path,payload):
        packet=payload['packet'];index=packet['review_scope']['unit']['section_index'];mode=self.modes.get(index,'ok')
        self.calls.append((path,index))
        if path.endswith('/prepare'):
            if self.interrupt and index==0:self.interrupt=False;raise Interrupted('Synthetic interruption only')
            result={'version':VERSION,'packet_sha256':packet['packet_sha256'],
                'status':'unavailable_input_limit'if mode=='oversize'else'prepared','purpose':'SYNTHETIC_ONLY'}
            result['preparation_sha256']=digest(result);return result
        section=next(s for s in packet['sections']if s['index']==index);text=section['evidence_draft'];a=text.index('je je')
        row={'kind':'additional_suggestion','section_index':index,'start':a,'end':a+5,'observed_text':'je je',
            'replacement':'je','explanation':'Synthetic mechanical edit only; not model language evidence.',
            'uncertainty':'','finding_ids':[],'source_refs':[]}
        if mode=='escape':row['section_index']=0
        raw={'items':[]if mode in {'empty','uncertain'}else[row]}
        result={'version':VERSION,'packet_sha256':packet['packet_sha256'],'evidence_manifest_sha256':packet['evidence_manifest_sha256'],
            'preparation_sha256':'0'*64 if mode=='wrong_preparation'else payload['prepared']['preparation_sha256'],
            'origin':'ai_generated','authority':'not_source_authorized','status':'unavailable'if mode=='uncertain'else'available',
            'attempted':True,'inference_performed':None if mode=='uncertain'else False,'retries':0,
            'raw_content':json.dumps(raw),'items':[],'coverage':packet['coverage']}
        if mode!='escape':result['items']=checked_output(raw,packet)
        result['result_sha256']=digest(result);return result


def multi(rig,modes=None,count=2,interrupt=False):
    app,client,documents,_,headers,_,_=setup(rig)
    gateway=ScopedGateway(modes,interrupt);documents.domain.request_gateway=gateway
    previous=rig[2].generate
    rig[2].generate=lambda bundle,tier,model,parent=None:ai.generate(gateway,bundle,tier,model,parent)if bundle.get('contract')==ai.CONTRACT else previous(bundle,tier,model,parent)
    from test_document_word import package
    paragraphs=''.join('<w:p><w:r><w:t>Oseba je je doma.</w:t></w:r></w:p>'for _ in range(count))
    data=package(paragraphs,{'word/media/preserved.bin':b'UNCHANGED SYNTHETIC PART'})
    response=client.post('/api/pilot/documents/upload',content=data,headers={**headers,'Content-Type':'application/octet-stream',
        'X-Document-Name':'scope-control.docx','X-Upload-Key':'scoped-control'})
    assert response.status_code==200;identifier=response.json()['document_id'];assert start(client,headers,identifier).status_code==200
    finish(app);root='/api/pilot/documents/'+identifier;evidence=client.get(root).json()
    with documents.store.connect()as db:owner=db.execute('SELECT owner FROM uploaded_documents WHERE id=?',(identifier,)).fetchone()[0]
    return app,client,documents,gateway,headers,identifier,root,evidence,owner


def prepare_start(env):
    app,client,documents,gateway,headers,identifier,root,evidence,owner=env
    assert client.post(root+'/ai/prepare',headers=headers,json={}).status_code==200
    queued=client.post(root+'/ai/start',headers=headers,json={});assert queued.status_code==200,queued.text
    return queued.json()['ai_suggestions']


def test_partial_results_report_accept_undo_and_labelled_word_keep_evidence_immutable(rig):
    env=multi(rig,{1:'oversize'});app,client,documents,gateway,headers,identifier,root,evidence,owner=env
    base=client.get(root+'/draft/docx').content;prepare_start(env);finish(app)
    state=client.get(root).json()['ai_suggestions']
    assert state['state']=='partial'and not state['review_complete']and state['finished']
    assert [u['state']for u in state['units']]==['completed','unavailable_input_limit']
    item,=state['items'];accepted=decide(client,headers,identifier,state,item,'accept');assert accepted.status_code==200
    selected=accepted.json()['ai_suggestions']['selected_revision'];revision=selected['revision_id']
    assert selected['partial_advice']and client.get(root+'/selected/docx?revision='+revision).status_code==409
    response=client.get(root+'/selected/docx?revision='+revision+'&partial=true');assert response.status_code==200
    with ZipFile(io.BytesIO(response.content))as word:
        body=word.read('word/document.xml').decode();assert 'Delni AI pregled'in body
        assert 'Oseba je doma.'in body and 'Oseba je je doma.'in body
        assert word.read('word/media/preserved.bin')==b'UNCHANGED SYNTHETIC PART'
    report=client.get(root+'/export/txt').text;assert 'Delni AI pregled'in report and 'omejitev'in report
    assert client.get(root+'/draft/docx').content==base
    assert client.get(root).json()['review_manifest']==evidence['review_manifest']
    assert documents.get(owner,identifier)['ai_suggestions']['selected_revision']['revision_id']==revision
    assert decide(client,headers,identifier,state,item,'undecided').json()['ai_suggestions']['selected_revision']is None


def test_empty_and_uncertain_units_stay_honest_and_never_retry(rig):
    env=multi(rig,{0:'empty',1:'uncertain'});app,client,_,gateway,headers,_,root,_,_=env
    prepare_start(env);finish(app);state=client.get(root).json()['ai_suggestions']
    assert [u['state']for u in state['units']]==['empty','unavailable']and state['state']=='partial'and state['items']==[]
    assert state['units'][1]['attempted']and state['units'][1]['inference_performed']is None
    for _ in range(2):assert client.post(root+'/ai/start',headers=headers,json={}).status_code==200
    finish(app);assert sum(not p.endswith('/prepare')for p,i in gateway.calls)==2


def test_existing_scheduler_obeys_queue_capacity_and_finishes_all_started_units_once(rig):
    env=multi(rig,count=5);app,client,documents,gateway,headers,_,root,_,_=env
    documents.domain.per_owner=1;state=prepare_start(env)
    assert sum(u['family_id']is not None for u in state['units'])==1
    assert not state['can_start']and state['can_cancel']
    finish(app);state=client.get(root).json()['ai_suggestions']
    assert state['state']=='available'and state['review_complete']and state['completed_units']==5
    assert len({u['family_id']for u in state['units']})==5
    assert sum(not p.endswith('/prepare')for p,i in gateway.calls)==5


def test_cancel_stops_pending_queue_without_altering_evidence(rig):
    env=multi(rig,count=3);app,client,documents,gateway,headers,_,root,evidence,_=env
    documents.domain.per_owner=1;prepare_start(env)
    cancelled=client.post(root+'/ai/cancel',headers=headers,json={});assert cancelled.status_code==200
    finish(app);state=client.get(root).json()['ai_suggestions']
    assert state['state']=='cancelled'and all(u['state']=='cancelled'for u in state['units'])
    assert all(p.endswith('/prepare')for p,i in gateway.calls)
    assert client.get(root).json()['review_manifest']==evidence['review_manifest']


def test_global_dispatch_bound_leaves_remaining_parts_visibly_unavailable(rig):
    env=multi(rig,count=10);app,client,_,gateway,_,_,root,_,_=env
    prepare_start(env);finish(app);state=client.get(root).json()['ai_suggestions']
    assert state['state']=='partial'and state['completed_units']==8 and state['total_units']==10
    assert all(u['state']=='unavailable_dispatch_limit'and not u['family_id']for u in state['units'][8:])
    assert sum(not p.endswith('/prepare')for p,i in gateway.calls)==8
    assert sum(u['item_limit']for u in state['units'])==state['global_suggestion_limit']==24


@pytest.mark.parametrize('cancel',[False,True])
def test_interruption_after_family_acceptance_recovers_same_job_or_cancels_it(rig,monkeypatch,cancel):
    env=multi(rig);app,client,documents,gateway,headers,identifier,root,_,owner=env
    documents.prepare_ai(owner,identifier);save=ai._save_stage;crash=[True]
    def interrupted(*args):
        stage=args[-1]
        if crash[0]and stage['units'][0].get('family_id'):crash[0]=False;raise Interrupted('Accepted before linking')
        return save(*args)
    monkeypatch.setattr(ai,'_save_stage',interrupted)
    with pytest.raises(Interrupted):documents.start_ai(owner,identifier)
    monkeypatch.setattr(ai,'_save_stage',save)
    if cancel:documents.cancel_ai(owner,identifier)
    else:documents.start_ai(owner,identifier)
    finish(app);state=client.get(root).json()['ai_suggestions']
    assert state['state']==('cancelled'if cancel else'available')
    assert sum(not p.endswith('/prepare')for p,i in gateway.calls)==(0 if cancel else 2)
    assert len({u['family_id']for u in state['units']if u['family_id']})==(1 if cancel else 2)


def test_interrupted_preparation_is_not_retried_but_pending_part_can_prepare(rig):
    env=multi(rig,interrupt=True);app,client,documents,gateway,headers,identifier,root,_,owner=env
    with pytest.raises(Interrupted):documents.prepare_ai(owner,identifier)
    state=documents.prepare_ai(owner,identifier)['ai_suggestions']
    assert [u['state']for u in state['units']]==['failed','prepared']
    assert [i for p,i in gateway.calls if p.endswith('/prepare')]==[0,1]
    documents.start_ai(owner,identifier);finish(app)
    assert client.get(root).json()['ai_suggestions']['state']=='partial'


def test_cancellation_winning_during_job_link_cannot_be_overwritten(rig,monkeypatch):
    from pilot_backend.security import Failure
    env=multi(rig);app,client,documents,gateway,_,identifier,root,_,owner=env
    documents.prepare_ai(owner,identifier);save=ai._save_stage;once=[True]
    def cancel_during_link(*args):
        stage=args[-1]
        if once[0]and stage['units'][0].get('family_id'):
            once[0]=False;ai.cancel(documents,owner,identifier)
        return save(*args)
    monkeypatch.setattr(ai,'_save_stage',cancel_during_link)
    with pytest.raises(Failure):documents.start_ai(owner,identifier)
    finish(app);state=client.get(root).json()['ai_suggestions']
    assert state['cancel_requested']and state['state']=='cancelled'
    assert all(p.endswith('/prepare')for p,i in gateway.calls)


@pytest.mark.parametrize('mode',['escape','wrong_preparation'])
def test_invalid_unit_output_is_not_aggregated_and_stale_parent_blocks_acceptance(rig,mode):
    env=multi(rig,{1:mode});app,client,documents,_,headers,identifier,root,_,_=env
    prepare_start(env);finish(app);state=client.get(root).json()['ai_suggestions']
    assert state['state']=='partial'and [u['state']for u in state['units']]==['completed','failed']
    assert len(state['items'])==1 and state['items'][0]['section_index']==0
    with documents.store.tx()as db:db.execute('UPDATE uploaded_documents SET source_sha256=?WHERE id=?',('f'*64,identifier))
    assert client.get(root).json()['ai_suggestions']['stale']
    assert decide(client,headers,identifier,state,state['items'][0],'accept').status_code==409
