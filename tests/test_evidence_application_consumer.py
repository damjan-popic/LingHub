"""Paired complete offline CORE→WEB consumer controls, no inference/network.

Retained actual comma proof is replayed, not invented. The repeated-word
fixture uses explicitly synthetic contextual observation and retained native
source; it is a mechanical integration test, not another language run.
"""
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import os,json,sys,re,importlib.util
import pytest
CORE=Path(os.environ.get('KLIKAI_PINNED_CORE_ROOT',''))
if not (CORE/'tools/zrc_reference/pilot_documents.py').is_file():
    pytest.skip('Set explicit paired CORE for complete source-application consumer controls',allow_module_level=True)
sys.path.insert(0,str(CORE));sys.path.insert(0,str(CORE/'apps/api'))
from tools.zrc_reference import document_evidence_review as producer,pilot_unified_evidence as sources
from tools.zrc_reference.pilot_documents import DocumentTransformRequest
from tools.zrc_reference.common import value_hash
from pilot_backend import document_transform as consumer,unified
from pilot_backend.security import Failure

HERE=CORE/'tests/zrc/fixtures'
def load(name):return json.loads((HERE/name).read_text())
def synthetic_syntax(text):
    words=[{'id':i,'start':m.start(),'end':m.end(),'text':m.group(),'upos':'ADJ','lemma':m.group().lower(),
            'head_id':0,'deprel':'root'}for i,m in enumerate(re.finditer(r'\w+|[^\w\s]',text),1)]
    observation={'schema':'linghub.syntax-observation.v1','status':'ok','input_text':text,'input_sha256':producer.sha(text),
        'target_scope':{'scope':'question','start':0,'end':len(text),'text':text},
        'analysis':{'text':text,'offset_convention':'unicode_codepoint_half_open','head_convention':'one_based_0_root','words':words}}
    capture={'operation':'syntax','request':{'text':text},'response':observation,'response_sha256':value_hash(observation)}
    return {'version':'klikai.syntax-collation.v1','plan':{'status':'ready','routing_kind':'comma_explanation_syntax'},
            'captures':[capture],'capture_set_sha256':value_hash([capture])}

def fixture(kind):
    if kind=='comma':
        retained=load('document_comma_fitting_20261111.json');observation=load('document_retained_checker_20261113.json')['observation']
        text=observation['input_text'];captures=deepcopy(retained['unified_evidence']);policy=retained['unified_policy']
        observations={'comma':observation,'syntax':retained['tool_evidence']}
    else:
        text='Nadaljni opis ostane jasen; navedek »Nadaljni« je ohranjen.'
        rows=[deepcopy(load('document_native_advice_20261113.json')['records'][0])]
        captures={'version':sources.VERSION,'matches':rows,'retrieval':[]}
        policy={'policy_id':'OFFLINE-paired-test','sources':['cu-slogovni'],'editorial_operator_policy_id':'OFFLINE-paired-test'}
        observations={'syntax':synthetic_syntax(text)}
    task={'mode':'review','source_language':'sl','target_language':None,'locale':'sl','source_text':text,
          'source_sha256':producer.sha(text),'review_strategy':producer.STRATEGY,
          'document_binding':{'document_id':'OFFLINE-consumer-'+kind,'section_index':0},
          'document_revision':'0'*64,'document_location':'OFFLINE-retained-original-coordinates'}
    normalized,_=sources.validate_capture_set(captures,{'unified_policy':policy})
    rows=[]
    for record in normalized['matches']:
        eid=sources.evidence_id(record)
        rows.append({**record,'evidence_id':eid,'source':{'source_id':record['source_slug']+':'+record['external_id'],
                    'source_revision':record['capture_sha256'],'locator':{}}})
    source_bundle={'contract':unified.CONTRACT,'matches':rows}
    source_bundle['snapshot_sha256']=value_hash(source_bundle)
    bundle={'contract':consumer.CONTRACT,'document_task':task,'unified_evidence':captures,'source_bundle':source_bundle,
            'review_observations':observations,'retrieval':{'status':'OFFLINE-retained-fixture'}}
    bundle['snapshot_sha256']=value_hash(bundle)
    runtime=SimpleNamespace(settings={'unified_policy':policy}) # No provider or analyzer exists.
    calls=[]
    def request(path,payload):
        assert path=='/internal/pilot/document-transform';calls.append(path)
        return producer.transform(runtime,DocumentTransformRequest(**payload))
    return bundle,SimpleNamespace(request=request),calls

@pytest.mark.parametrize('kind',['comma','repeated'])
def test_complete_writer_free_consumer_accepts_bound_operations(kind):
    bundle,gateway,calls=fixture(kind)
    result=consumer._generate_evidence(gateway,bundle,'short','cpu')
    assert calls==['/internal/pilot/document-transform']and result['provider_attempts']==[]
    document=result['document_result'];assert document['edits']
    if kind=='comma':assert any(e['start']==e['end']and not e['original']for e in document['edits'])
    else:
        assert bundle['document_task']['source_text'].count('Nadaljni')==2
        assert len(document['edits'])==1 and '»Nadaljni«'in document['proposed_text']
    matches={r['evidence_id']:r for r in bundle['source_bundle']['matches']}
    assert consumer.validate_document_result(document,bundle['document_task'],matches)==document

def test_complete_consumer_keeps_proof_span_source_noop_and_reconstruction_checks():
    bundle,gateway,_=fixture('repeated');document=consumer._generate_evidence(gateway,bundle,'short','cpu')['document_result']
    matches={r['evidence_id']:r for r in bundle['source_bundle']['matches']}
    for defect in ('span','proof','source','noop','output'):
        bad=deepcopy(document)
        if defect=='span':bad['edits'][0]['start']+=1
        elif defect=='proof':next(f for f in bad['review']['findings']if f['included_in_draft'])['application_proof']['input_sha256']='9'*64
        elif defect=='source':next(f for f in bad['review']['findings']if f['included_in_draft'])['source']['focus']['text']='different'
        elif defect=='noop':bad['edits'][0]['replacement']=bad['edits'][0]['original']
        else:bad['proposed_text']+='x'
        with pytest.raises(Failure):consumer.validate_document_result(bad,bundle['document_task'],matches)

@pytest.mark.parametrize('mode',['review','translate'])
def test_complete_consumer_keeps_writer_and_translation_unique_original_restriction(mode):


    # Existing generic consumer rules are exercised by their own retained
    # contract suite as well; this negative has no evidence-review escape.
    bundle,gateway,_=fixture('repeated');document=consumer._generate_evidence(gateway,bundle,'short','cpu')['document_result']
    generic=deepcopy(document);generic.pop('review');generic['validation_scope']='structure_exact_source_spans_source_membership_and_protected_content_only'
    generic['mode']=mode;generic['target_language']='it'if mode=='translate'else None
    if mode=='translate':generic['validation_scope']='translation_structure_and_protected_content_only'
    plain_task=deepcopy(bundle['document_task'])
    plain_task['mode']=mode;plain_task['target_language']=generic['target_language']
    for key in ('review_strategy','document_binding','document_revision','document_location'):plain_task.pop(key,None)
    matches={r['evidence_id']:r for r in bundle['source_bundle']['matches']}
    with pytest.raises(Failure):consumer.validate_document_result(generic,plain_task,matches)


def test_duplicate_insertion_points_fail_after_valid_proof_checks():
    bundle,gateway,_=fixture('comma')
    document=consumer._generate_evidence(gateway,bundle,'short','cpu')['document_result']
    bad=deepcopy(document);first=next(f for f in bad['review']['findings']if f['included_in_draft'])
    second=deepcopy(first);second['finding_id']='F'+'f'*24
    bad['review']['findings'].append(second)
    bad['edits'].append(deepcopy(first['proposed_edit']))
    bad['review']['applications']['edits']=deepcopy(bad['edits'])
    bad['review']['overview']['included_edits']=2
    bad['review']['overview']['counts']['executable_application']=2
    text=bundle['document_task']['source_text'];edit=first['proposed_edit']
    assert edit['start']==edit['end']
    bad['proposed_text']=text[:edit['start']]+edit['replacement']*2+text[edit['end']:]
    bad['review']['manifest_sha256']=value_hash({k:v for k,v in bad['review'].items()if k!='manifest_sha256'})
    consumer.checked_result(bad) # Both retained application proofs still check.
    matches={r['evidence_id']:r for r in bundle['source_bundle']['matches']}
    with pytest.raises(Failure):consumer.validate_document_result(bad,bundle['document_task'],matches)
