"""Synthetic producer-body receiving contract, no source/model calls."""
from copy import deepcopy
import hashlib
import pytest
from pilot_backend.document_source_body import source_body,body_binding
from pilot_backend.evidence_review import checked
from pilot_backend.store import digest
from pilot_backend.security import Failure
from test_document_evidence_review import sample

def test_editorial_full_body_binding_survives_consumer_validation():
    task,doc,rows=sample();review=doc['review'];finding=review['findings'][0]
    raw={'title':'Synthetic Q/A','question_original':'Vprašanje?',
       'answer_text':'Odgovor velja le pod pogojem A.','additional_info':'Druga možnost B.'}
    record=rows['E1'];record.update(capture_kind='editorial_record',capture=raw,capture_sha256=digest(raw))
    body=source_body(record);source=finding['source'];source.update(capture_sha256=digest(raw),body_binding=body_binding(record),
       focus={'char_start':0,'char_end':len(body),'text':body,'text_sha256':hashlib.sha256(body.encode()).hexdigest()})
    finding['related_source_captures'][0]['capture_sha256']=digest(raw)
    review['manifest_sha256']=digest({k:v for k,v in review.items()if k!='manifest_sha256'})
    assert checked(review,task['source_text'],task,rows)==review
    bad=deepcopy(review);bad['findings'][0]['source']['body_binding']['rendering']='invented'
    bad['manifest_sha256']=digest({k:v for k,v in bad.items()if k!='manifest_sha256'})
    with pytest.raises(Failure):checked(bad,task['source_text'],task,rows)

def test_ai_packet_retains_native_candidate_context_with_no_evidence_finding():
    import json
    from types import SimpleNamespace
    from pilot_backend import document_ai_suggestions as ai
    from pilot_backend.document_resource_context import VERSION
    text='Hiter vlak je prispel.';sha=hashlib.sha256(text.encode()).hexdigest()
    native={'passage_id':'tool:synthetic-sense','source_id':'native:sense-1','source_slug':'cjvt-thesaurus',
        'source_revision':'2'*64,'role':'tool_result','tool_kind':'synonyms','text':'Complete source relation.',
        'context_eligible':False,'original_json':{'target':'Hiter','fields':{'sense':{'examples':['Hiter vlak'],'domain':'railway','relations':[{'raw_text':'ekspresen'}]}}}}
    context={'version':VERSION,'source_sha256':sha,'envelopes':[{'occurrence':{'section':0,'start':0,'end':5,
        'text':'Hiter','original_sha256':sha},'language':'sl','source_passages':[],'tool_passages':[native]}]}
    context['context_sha256']=digest(context)
    bundle={'source_bundle':{'matches':[]},'review_observations':{'ordinary_resources':context}}
    class DB:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):return self
        def fetchone(self):return (json.dumps(bundle),)
    documents=SimpleNamespace(store=SimpleNamespace(connect=DB))
    document={'state':'complete','document_id':'offline','source_sha256':'3'*64,'warnings':[],
        'options':{'review_strategy':'evidence_review_v1','source_language':'sl','locale':'sl'},
        'review_manifest':{'manifest_sha256':'4'*64},'sections':[{'index':0,'state':'complete','family_id':'fake',
        'location':'text@0:21','text':text,'document_result':{'status':'draft','proposed_text':text,'edits':[],
        'review':{'manifest_sha256':'5'*64,'findings':[],'checks':[]}}}]}
    value=ai.packet(documents,'owner',document);section,=value['sections'];assert section['findings']==[]
    assert len(section['retained_context'])==1
    source,=value['sources'].values();assert source['native_source_unit']==native
    assert source['native_answer_eligibility']is False
    assert source['admission_roles']==['model_context_not_edit_authority']
    assert value['coverage']['resource_context_delivery'][0]['additional_context_count']==1
    # A domain label alone does not bind this occurrence to a source sense.
    native['original_json']['fields']['sense'].pop('examples')
    context['context_sha256']=digest({k:v for k,v in context.items()if k!='context_sha256'})
    unscoped=ai.packet(documents,'owner',document)
    assert unscoped['sources']=={}
    assert unscoped['coverage']['resource_context_delivery'][0]['omitted'][0]['reason']=='unscoped_synonym_senses_retained_in_candidate_trace'
    native['incomplete']=True;native['context_eligible']=True
    context['context_sha256']=digest({k:v for k,v in context.items()if k!='context_sha256'})
    blocked=ai.packet(documents,'owner',document)
    assert blocked['sources']=={}and blocked['sections'][0]['retained_context']==[]
    assert blocked['coverage']['resource_context_delivery'][0]['omitted'][0]['reason']=='native_content_incomplete_or_missing'


def test_owned_document_reader_keeps_native_context_and_owner_boundary(tmp_path):
    import json
    from argon2 import PasswordHasher,Type
    from fastapi.testclient import TestClient
    from pilot_backend.api import create_app
    from pilot_backend import document_transform as doc,unified
    from pilot_backend.document_resource_context import VERSION
    from test_document_transform import Gateway,task,response
    class NativeReaderGateway(Gateway):
        reader_contracts=[]
        def freeze(self,family,policy):
            value=doc.freeze(self,family,policy);b=value['bundle'];text=b['document_task']['source_text']
            sha=hashlib.sha256(text.encode()).hexdigest()
            native={'passage_id':'tool:owned-native','source_id':'native:owned','source_slug':'cjvt-thesaurus',
                'source_revision':'2'*64,'role':'tool_result','tool_kind':'synonyms','text':'Complete retained native content.',
                'context_eligible':False,'incomplete':True}
            context={'version':VERSION,'source_sha256':sha,'envelopes':[{'occurrence':{'section':0,'start':0,'end':len(text),
                'text':text,'original_sha256':sha},'language':'sl','tool_passages':[native]}]}
            context['context_sha256']=digest(context);b['review_observations']={'ordinary_resources':context}
            b['snapshot_sha256']=digest({k:v for k,v in b.items()if k!='snapshot_sha256'})
            self.result=response(b);return value
        def generate(self,bundle,tier,model,parent=None):return doc.generate(self,bundle,tier,model,parent)
        def source(self,bundle,eid,tier):
            self.reader_contracts.append(bundle.get('contract'))
            return doc.source(bundle,eid,tier)if bundle.get('contract')==doc.CONTRACT else unified.source(bundle,eid)
    origin='http://127.0.0.1:18310';adapter=NativeReaderGateway()
    app=create_app({'store':str(tmp_path/'pilot.sqlite'),'allowed_origins':[origin],'secure_cookies':False,
        'unified_answers':True,'models':['auto'],'klikai_url':'http://127.0.0.1:18313'},adapter=adapter,start_workers=False,
        hasher=PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1,type=Type.ID))
    password='synthetic native reader password'
    owner=app.state.auth.create('reader-one',password,role='tester',origin='synthetic',must_change=False)
    app.state.auth.create('reader-two',password,role='tester',origin='synthetic',must_change=False)
    with TestClient(app,base_url=origin)as client:
        assert client.post('/api/pilot/login',json={'username':'reader-one','password':password},headers={'Origin':origin}).status_code==200
        created=app.state.domain.create_family(owner,{'question':'Pregled naloženega dokumenta','locale':'sl','input_language':'sl',
            'model':'auto','task':'reference','idempotency_key':'native-reader-owner-test'},document_task=task())
        app.state.jobs.execute(app.state.jobs.claim());fid=created['family']['answer_family_id']
        url='/api/pilot/families/'+fid+'/sources/tool:owned-native?tier=answer'
        opened=client.get(url);assert opened.status_code==200,opened.text
        assert 'Complete retained native content.'in opened.json()['excerpt']
        assert opened.json()['reader_blocks'][0]['heading']=='Omejitev zajetega gradiva'
        assert adapter.reader_contracts[-1]==doc.CONTRACT
        assert client.post('/api/pilot/login',json={'username':'reader-two','password':password},headers={'Origin':origin}).status_code==200
        assert client.get(url).status_code==404


def test_collocation_nomination_uses_original_native_sentence_and_keeps_uncertainty():
    from pilot_backend.document_ai_suggestions import context_nomination
    text='Pripravili smo pogodbo.'
    occurrence={'start':15,'end':22,'text':'pogodbo'}
    passage={'tool_kind':'collocations','original_json':{'fields':{'headword':'pogodba',
        'surface':'pripraviti pogodbo','components':[{'lemma':'pripraviti'},{'lemma':'pogodba'}]}}}
    record={'capture':{},'capture_kind':'ordinary_context_unit','rights_mode':'owned',
        'model_context_eligible':True,'context_eligible':False,'native_passage':passage,'occurrences':[occurrence]}
    task={'source_text':text,'source_language':'sl'}
    words=[{'start':0,'end':10,'lemma':'pripraviti','sentence_id':0},
           {'start':15,'end':22,'lemma':'pogodba','sentence_id':0}]
    from copy import deepcopy
    before=deepcopy(record)
    nomination=context_nomination(record,task,words)
    assert nomination['eligible']and nomination['applicability_not_established']
    assert record==before
    words[0]['sentence_id']=1
    assert not context_nomination(record,task,words)['eligible']
    words[0]['sentence_id']=0;words[1]['lemma']='other'
    assert not context_nomination(record,task,words)['eligible']
