"""EX01 and explicit history access: mechanical mocks, no actual operations."""
from copy import deepcopy
import json
from pilot_backend import document_ai_suggestions as ai,document_evidence_explanations as e
from pilot_backend.document_ai_outcomes import presentation
from test_document_uploads import rig,upload,start,finish
from test_document_ai_suggestions import setup,generate_ai,decide


def owner_and_options(documents,identifier):
    with documents.store.connect()as db:
        row=db.execute('SELECT owner,options FROM uploaded_documents WHERE id=?',(identifier,)).fetchone()
    return row['owner'],json.loads(row['options'])


def jobs(documents):
    with documents.store.connect()as db:return {table:db.execute('SELECT COUNT(*)FROM '+table).fetchone()[0]for table in ('families','jobs','runs')}


def test_source_free_heading_reaches_honest_terminal_without_native_preparation_or_job(rig):
    app,client,documents,gateway,headers,_,_=setup(rig)
    doc=upload(client,headers,b'Potrdilo o vpisu',key='MECHANICAL-empty-heading');identifier=doc['document_id']
    assert start(client,headers,identifier).status_code==200;finish(app)
    root='/api/pilot/documents/'+identifier;before=client.get(root).json();counts=jobs(documents)
    prepared=client.post(root+'/ai/prepare',headers=headers,json={'purpose':e.PURPOSE});assert prepared.status_code==200,prepared.text
    public=prepared.json()['ai_suggestions'];assert public['state']==e.NO_EVIDENCE and public['finished']and public['review_complete']
    assert not public['can_start']and not public['can_cancel']and not public['can_prepare']
    assert public['completed_units']==0 and public['total_units']==1 and public['origin']=='server_determined'
    unit,=public['units'];assert unit['state']==e.NO_EVIDENCE and unit['attempted']is False and unit['inference_performed']is False
    assert not unit['family_id']and not unit['job_id']and not unit['run_id']and unit['preparation_sha256']is None
    assert public['presentation']['units'][unit['unit_id']]['kind']==e.NO_EVIDENCE
    assert gateway.calls==[]and jobs(documents)==counts
    assert client.post(root+'/ai/start',headers=headers,json={}).json()['ai_suggestions']['state']==e.NO_EVIDENCE
    assert gateway.calls==[]and jobs(documents)==counts
    after=client.get(root).json();assert after['sections']==before['sections']and after['review_manifest']==before['review_manifest']
    for locale in ('sl','it'):
        text='\n'.join(str(v)for _,v in ai.report_blocks(public,locale))
        assert ('AI ni bila uporabljena'if locale=='sl'else'IA non utilizzata')in text
        assert 'items:[]'not in text and 'ValueError'not in text
    # A mechanically staged legacy prepared zero-anchor unit must not reach
    # create_family either. This is a synthetic boundary control, not readiness.
    owner,options=owner_and_options(documents,identifier);stage=options['ai_review'];stage['state']='prepared'
    stage['units'][0].update(state='prepared',prepared={'preparation_sha256':'MECHANICAL-NOT-NATIVE'})
    ai._save_stage(documents,owner,identifier,stage)
    assert client.post(root+'/ai/start',headers=headers,json={}).json()['ai_suggestions']['state']==e.NO_EVIDENCE
    assert gateway.calls==[]and jobs(documents)==counts


def test_explicit_terminal_purpose_action_archives_exact_prior_stage_and_decisions(rig):
    _,client,documents,gateway,headers,identifier,_,after=generate_ai(rig)
    public=after['ai_suggestions'];assert public['can_request_explanations']is True
    assert decide(client,headers,identifier,public,public['items'][0],'dismiss').status_code==200
    _,before=owner_and_options(documents,identifier);before=deepcopy(before);calls=list(gateway.calls)
    response=client.post('/api/pilot/documents/'+identifier+'/ai/prepare',headers=headers,json={'purpose':e.PURPOSE})
    assert response.status_code==200,response.text
    _,options=owner_and_options(documents,identifier);history=options['ai_history'][-1]
    assert history=={**before['ai_review'],'decisions':before['ai_decisions'],'decision_history':before['ai_decision_history']}
    assert options['ai_decisions']=={}and options['ai_review']['packet']['writer_purpose']==e.PURPOSE
    assert gateway.calls==calls and response.json()['ai_suggestions']['state']==e.NO_EVIDENCE


def test_active_independent_review_cannot_be_switched_or_interrupted(rig):
    _,client,documents,gateway,headers,identifier,_=setup(rig);root='/api/pilot/documents/'+identifier
    prepared=client.post(root+'/ai/prepare',headers=headers,json={});assert prepared.status_code==200
    assert not prepared.json()['ai_suggestions'].get('can_request_explanations')
    _,before=owner_and_options(documents,identifier);calls=list(gateway.calls)
    response=client.post(root+'/ai/prepare',headers=headers,json={'purpose':e.PURPOSE})
    assert response.status_code==409
    assert owner_and_options(documents,identifier)[1]==before and gateway.calls==calls


def no_evidence_public():
    public={'state':e.NO_EVIDENCE,'writer_purpose':e.PURPOSE,'stale':False,'items':[],'explanation_anchors':[],
        'coverage':{'provided_sections':[0]},'decisions':{},'source_references':{},'review_complete':True,
        'can_start':False,'can_cancel':False,'can_prepare':False,'finished':True,
        'units':[{'unit_id':'MECHANICAL-NO-EVIDENCE','state':e.NO_EVIDENCE,'section_index':0,
            'original_span':{'start':0,'end':len('Potrdilo o vpisu'),'text':'Potrdilo o vpisu'},'attempted':False,'inference_performed':False}]}
    public['presentation']=presentation(public);return public


def test_old_explanation_representation_is_archived_then_rebuilt_without_reusing_cache(rig):
    _,client,documents,gateway,headers,identifier,evidence=setup(rig)
    root='/api/pilot/documents/'+identifier;assert client.post(root+'/ai/prepare',headers=headers,json={'purpose':e.PURPOSE}).status_code==200
    owner,options=owner_and_options(documents,identifier)
    old=options['ai_review'];old.pop('representation_policy')
    for unit in old['units']:unit.pop('representation_policy')
    def store(value):
        with documents.store.tx()as db:
            db.execute('UPDATE uploaded_documents SET options=?WHERE id=?',(json.dumps(value),identifier))
    store(options);before=deepcopy(options);counts=jobs(documents);report=client.get(root+'/export/txt').text
    response=client.post(root+'/ai/prepare',headers=headers,json={'purpose':e.PURPOSE});assert response.status_code==200,response.text
    _,after=owner_and_options(documents,identifier)
    assert after['ai_history'][-1]=={**before['ai_review'],'decisions':before.get('ai_decisions',{}),'decision_history':before.get('ai_decision_history',[])}
    assert after['ai_review']['representation_policy']==e.REPRESENTATION
    assert all(u['representation_policy']==e.REPRESENTATION for u in after['ai_review']['units'])
    assert after['ai_review']['packet']==old['packet']and gateway.calls==[]and jobs(documents)==counts
    assert client.get(root+'/export/txt').text==report and client.get(root).json()['sections']==evidence['sections']
    # A stale, active stage cannot be replaced or dispatched under a new
    # renderer. Its exact old data stays available for normal cancellation.
    active=deepcopy(before);active['ai_review']['state']='prepared'
    active['ai_review']['units'][0]['state']='prepared';store(active)
    assert client.post(root+'/ai/prepare',headers=headers,json={'purpose':e.PURPOSE}).status_code==409
    assert client.post(root+'/ai/start',headers=headers,json={}).status_code==409
    assert owner_and_options(documents,identifier)[1]==active and gateway.calls==[]and jobs(documents)==counts


def test_explanation_job_key_binds_new_request_and_preserves_historical_keys():
    packet={'writer_purpose':e.PURPOSE,'packet_sha256':'a'*64}
    historical={'packet':packet,'prepared':{'request_sha256':'b'*64}}
    old=ai._unit_key('document',historical);assert old=='document-ai-document-'+'a'*32
    current={**historical,'representation_policy':e.REPRESENTATION};key=ai._unit_key('document',current)
    assert key!=old and ai._unit_key('document',current)==key
    changed={**current,'prepared':{'request_sha256':'c'*64}}
    assert ai._unit_key('document',changed)!=key
    pending={**current,'prepared':None}
    assert ai._unit_key('document',pending)not in {old,key}
    independent={**current,'packet':{'packet_sha256':'a'*64}}
    assert ai._unit_key('document',independent)==old
