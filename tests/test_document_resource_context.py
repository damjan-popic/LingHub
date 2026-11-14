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
        'context_eligible':False,'original_json':{'target':'Hiter','fields':{'sense':{'domain':'railway','relations':[{'raw_text':'ekspresen'}]}}}}
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
    native['incomplete']=True;native['context_eligible']=True
    context['context_sha256']=digest({k:v for k,v in context.items()if k!='context_sha256'})
    blocked=ai.packet(documents,'owner',document)
    assert blocked['sources']=={}and blocked['sections'][0]['retained_context']==[]
    assert blocked['coverage']['resource_context_delivery'][0]['omitted'][0]['reason']=='native_content_incomplete_or_missing'
