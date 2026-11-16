"""Complete ordinary-resource envelopes fit the final capture and request.

All records are protocol fixtures; no source, model, database or service calls.
"""
from copy import deepcopy
import hashlib

import httpx
import pytest

from pilot_backend import document_resource_collection as collection
from pilot_backend import document_resource_context as context
from pilot_backend import document_transform as doc, unified
from pilot_backend.store import canonical, digest


def fixture(count=4, body_size=3000):
    words=['izraz'+str(i) for i in range(count)]
    text=' '.join(words);sha=hashlib.sha256(text.encode()).hexdigest();envelopes=[];start=0
    for i,word in enumerate(words):
        body='Pogoj A in izjema B ostaneta skupaj. '+('čžš '+str(i)+' ')*body_size
        passage={'passage_id':'reference:'+str(i),'source_id':'source:'+str(i),'source_slug':'jezikovna_svetovalnica',
            'source_revision':digest(['revision',i]),'text':body,'title':'Zapis z izjemo '+str(i),
            'role':'expert_answer','rights_mode':'open','context_eligible':True,
            'span':{'char_start':0,'char_end':len(body),'post_id':i},'context':{'question':'Kaj pomeni '+word+'?'}}
        occurrence={'section':0,'start':start,'end':start+len(word),'text':word,'original_sha256':sha};start+=len(word)+1
        envelopes.append({'occurrence':occurrence,'query':word,'language':'sl','source_passages':[passage],
            'tool_passages':[],'reference_response':{'contract':'klikai.reference-source-context.v1','source_only':True,
                'question':word,'passages':[deepcopy(passage)]},'coverage':[{'family':'jezikovna_svetovalnica','queried':True,'status':'ok'}]})
    value={'version':context.VERSION,'source_sha256':sha,'envelopes':envelopes,
        'coverage':[c for e in envelopes for c in e['coverage']],
        'lexical_discovery':{'whole_section_discovered':True,'requested':[],'omitted':[]},
        'generation':{'attempted':False,'inference_performed':False,'scope':'general_writer_only'}}
    value['context_sha256']=digest(value)
    return value,text


def test_small_complete_context_is_unchanged_and_keeps_original_hash():
    value,text=fixture(2,30);before=deepcopy(value)
    kept=collection.bounded_context(value,1000000)
    assert kept==value and value==before
    assert len(context.records(kept,text,value['source_sha256']))==2


def test_budget_selects_whole_native_envelopes_and_records_omitted_identities_not_absence():
    value,text=fixture();before=deepcopy(value)
    bound=collection.bounded_context(value,100000)
    assert 0<len(bound['envelopes'])<len(value['envelopes'])
    assert len(canonical(bound).encode())<=100000 and value==before
    old={digest(e):e for e in value['envelopes']}
    assert all(e==old[digest(e)] for e in bound['envelopes'])
    records=context.records(bound,text,value['source_sha256'])
    assert len(records)==len(bound['envelopes'])
    for record in records:
        assert 'Pogoj A in izjema B ostaneta skupaj.' in record['native_passage']['text']
        group=context.frozen_reader_group(record,bound)
        assert group and group['original_text']==record['native_passage']['text']
    omitted=bound['omitted_envelopes']
    assert len(omitted)+len(bound['envelopes'])==len(value['envelopes'])
    for item in omitted:
        original=value['envelopes'][item['original_envelope_index']]
        assert item['envelope_sha256']==digest(original)
        assert item['source_identities'][0]['source_revision']==original['source_passages'][0]['source_revision']
        assert item['reason']=='document_context_byte_bound'
    assert any(c.get('status')=='bounded_partial' and c.get('retained')is False for c in bound['coverage'])
    assert bound['capture_selection']['basis']=='complete_envelopes_in_input_order_not_source_authority'
    assert bound['context_sha256']==digest({k:v for k,v in bound.items()if k!='context_sha256'})


def test_unavailable_context_stays_unavailable_when_another_envelope_is_omitted():
    value,text=fixture();value['envelopes'][0]['coverage'][0]['status']='unavailable'
    value['coverage'][0]['status']='unavailable';value['context_sha256']=digest({k:v for k,v in value.items()if k!='context_sha256'})
    kept=collection.bounded_context(value,100000)
    assert kept['coverage'][0]['status']=='unavailable'
    assert all(p['passage_id'] in {r['evidence_id']for r in context.records(kept,text,value['source_sha256'])}
               for e in kept['envelopes']for p in e['source_passages'])


def test_existing_envelope_and_native_unit_limits_are_honored_without_cutting_envelopes():
    value,_=fixture(65,1)
    kept=collection.bounded_context(value,10000000)
    assert len(kept['envelopes'])==64
    assert kept['omitted_envelopes'][0]['reason']=='document_context_envelope_bound'
    value,_=fixture(2,1)
    value['envelopes'][1]['tool_passages']=[{'passage_id':'oversized:'+str(i),'source_slug':'sloleks',
        'source_id':'record:'+str(i),'source_revision':'a'*64,'text':'Celotni pogoj.'}for i in range(513)]
    value['context_sha256']=digest({k:v for k,v in value.items()if k!='context_sha256'})
    kept=collection.bounded_context(value,10000000)
    assert len(kept['envelopes'])==1 and kept['envelopes'][0]==value['envelopes'][0]
    assert kept['omitted_envelopes'][0]['reason']=='document_context_unit_bound'
    assert len(kept['omitted_envelopes'][0]['source_identities'])==514


def test_current_complete_omission_metadata_is_not_silently_discarded():
    value,_=fixture()
    with pytest.raises(ValueError,match='omission metadata'):
        collection.bounded_context(value,1)


def test_freeze_reserves_final_frozen_and_http_payload_bytes_before_optional_augmentation(monkeypatch):
    from test_document_transform import Gateway,family
    value,text=fixture(4,3000);seen=[]
    class Source(Gateway):
        document_resources=True
    def collect(gateway,task,discovery,*,byte_limit=None):
        seen.append(byte_limit)
        return collection.bounded_context(value,byte_limit)
    monkeypatch.setattr(collection,'collect',collect)
    monkeypatch.setattr(unified,'MAX_FROZEN_BYTES',100000)
    f=family('review',text);task=f['request_options']['document_task'];task['review_strategy']='evidence_review_v1'
    task.update(document_binding={'document_id':'fixture','section_index':0},document_revision='b'*64,
                document_location='text@0:'+str(len(text)))
    captured=doc.freeze(Source(),f,{})['bundle'];doc.checked(captured)
    payload=doc._evidence_payload(captured)
    wire=httpx.Request('POST','http://offline.invalid/internal/pilot/document-transform',json=payload).content
    assert seen and 0<seen[0]<100000
    assert len(canonical(captured).encode())<=100000
    assert len(wire)==len(canonical(payload).encode())<=100000
    assert captured['document_task']['source_text']==text
    assert captured['source_bundle']['matches']
    assert len(captured['review_observations']['ordinary_resources']['envelopes'])<4
