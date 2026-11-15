"""Offline mechanical delivery/export controls; no real model or sources."""
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
import json
import pytest
from pilot_backend import document_ai_suggestions as ai,document_evidence_explanations as e,document_evidence_relationships as relations
from pilot_backend.document_ai_contract import PACKET,VERSION,sha,digest,checked_output
from pilot_backend.document_ai_outcomes import presentation
from test_document_uploads import rig,finish
from test_document_ai_suggestions import setup,SyntheticGateway,decide


def mechanical_explanation():
    ref='S'+'1'*16;text='MECHANICAL alpha.'
    packet={'version':PACKET,'writer_purpose':e.PURPOSE,'document_id':'MECHANICAL_EXPLANATION_FIXTURE',
        'document_revision':'0'*64,'evidence_manifest_sha256':'1'*64,'locale':'sl',
        'sections':[{'index':0,'original_text':text,'original_sha256':sha(text),'evidence_draft':text,'draft_sha256':sha(text),
            'evidence_edits':[],'findings':[{'finding_id':'F-mechanical','source_refs':[ref],'match':{'observed':{'start':0,'end':len(text),'text':text}}}]}],
        'sources':{ref:{'original_text':'COMPLETE MECHANICAL SOURCE','body_sha256':sha('COMPLETE MECHANICAL SOURCE')}},'coverage':{'provided_sections':[0]}}
    packet['packet_sha256']=digest(packet);anchor=e.anchors(packet)[0]
    raw={'items':[{'anchor_id':anchor['anchor_id'],'explanation':'MECHANICAL EXPLANATION DISPLAY FIXTURE ONLY.',
        'uncertainty':'MECHANICAL CONDITIONAL TEXT ONLY.','source_refs':[ref]}]}
    public={'writer_purpose':e.PURPOSE,'explanation_anchors':[anchor],'state':'available','stale':False,'items':checked_output(raw,packet),
        'coverage':packet['coverage'],'decisions':{},'source_references':{ref:{'title':'MECHANICAL SOURCE','family_id':'fixture-family','evidence_id':'fixture-source'}}}
    public['presentation']=presentation(public)
    return packet,raw,public


class ExplanationGateway(SyntheticGateway):
    def request(self,path,payload):
        if path.endswith('/prepare'):return super().request(path,payload)
        self.calls.append(path);packet=payload['packet'];anchors=e.anchors(packet)
        raw={'items':[{'anchor_id':a['anchor_id'],'explanation':'MECHANICAL EXPLANATION ONLY.',
            'uncertainty':'','source_refs':a['source_refs']}for a in anchors]}
        result={'version':VERSION,'packet_sha256':packet['packet_sha256'],'evidence_manifest_sha256':packet['evidence_manifest_sha256'],
            'preparation_sha256':payload['prepared']['preparation_sha256'],'origin':'ai_generated','authority':'not_source_authorized',
            'status':'available','attempted':False,'inference_performed':False,'retries':0,'items':checked_output(raw,packet),
            'raw_content':json.dumps(raw),'coverage':packet['coverage']}
        result['result_sha256']=digest(result);return result


def test_existing_api_queue_report_and_draft_keep_explanations_comment_only(rig,monkeypatch):
    monkeypatch.setattr(ai,'context_nomination',lambda *a:{'eligible':True,'reason':'MECHANICAL FIXTURE NOMINATION','occurrences':[]})
    app,client,documents,_,headers,identifier,evidence=setup(rig);gateway=ExplanationGateway();documents.domain.request_gateway=gateway
    previous_freeze=rig[2].freeze;previous_generate=rig[2].generate
    rig[2].freeze=lambda family,policy:ai.freeze(gateway,family,policy)if family.get('request_options',{}).get('document_ai')else previous_freeze(family,policy)
    rig[2].generate=lambda bundle,tier,model,parent=None:ai.generate(gateway,bundle,tier,model,parent)if bundle.get('contract')==ai.CONTRACT else previous_generate(bundle,tier,model,parent)
    root='/api/pilot/documents/'+identifier;base=client.get(root+'/draft/docx').content
    prepared=client.post(root+'/ai/prepare',headers=headers,json={'purpose':e.PURPOSE});assert prepared.status_code==200,prepared.text
    assert prepared.json()['ai_suggestions']['writer_purpose']==e.PURPOSE
    assert client.post(root+'/ai/start',headers=headers,json={}).status_code==200
    finish(app);document=client.get(root).json();public=document['ai_suggestions'];item,=public['items']
    assert public['state']=='available'and item['replacement']is None and not item['eligible_for_accept']
    assert item['anchor_id']==public['explanation_anchors'][0]['anchor_id'] and item['finding_ids']
    rejected=decide(client,headers,identifier,public,item,'accept');assert rejected.status_code==422
    assert rejected.json()['error']['code']=='document_ai_ineligible'
    text=client.get(root+'/export/txt').text
    assert 'Razlaga AI'in text and 'MECHANICAL EXPLANATION ONLY.'in text and 'Sprejem ni jezikovno potrdilo.'not in text
    with ZipFile(BytesIO(client.get(root+'/export/docx').content))as word:
        assert b'MECHANICAL EXPLANATION ONLY.'in word.read('word/document.xml')
    assert client.get(root+'/draft/docx').content==base and document['sections']==evidence['sections'] and document['review_manifest']==evidence['review_manifest']
    assert gateway.calls.count('/internal/pilot/document-ai-suggestions')==1


def test_recorded_relation_values_and_unknowns_reach_writer_snapshot_without_mutation(monkeypatch):
    record={'evidence_id':'E','capture_sha256':'a'*64,'source_slug':'fixture','external_id':'record',
        'capture':{'original_text':'COMPLETE','source_revision':'b'*64,
            'native_table_projection':{'fixture':'MECHANICAL derivation binding'},
            'source_unit':{'original_json':{'provenance':{'version':'MECHANICAL-version'},'fields':{'sense':{
                'sense_id':'s1','relations':[{'relation_type':'synonym','synonym_type':'near','target_sense_id':'s2'}]},
                'provenance':{'archive_sha256':'c'*64,'source_entry_id':'entry'}}}}}}
    before=deepcopy(record);captured=relations.capture_relationships(record)
    ref='S'+'1'*16;snapshot=relations.snapshot({ref:{}},[],{ref:captured})
    facts=snapshot['by_source'][ref]['recorded_facts']
    assert any(f['value']==record['capture']['native_table_projection']for f in facts)
    assert any(f['value']==record['capture']['source_unit']['original_json']['fields']['sense']for f in facts)
    assert snapshot['by_source'][ref]['unknowns']and record==before
    assert all(f['origin']['capture_sha256']=='a'*64 for f in facts)


def test_client_cannot_inject_relationship_data_or_an_unrecognized_purpose(rig):
    _,client,_,gateway,headers,identifier,_=setup(rig)
    for body in ({'purpose':[]},{'purpose':{}},{'purpose':'invented'},
            {'purpose':e.PURPOSE,'source_relationships':{'invented':'hierarchy'}}):
        response=client.post('/api/pilot/documents/'+identifier+'/ai/prepare',headers=headers,json=body)
        assert response.status_code==409 and response.json()['error']['code']=='document_ai_unavailable'
    assert gateway.calls==[]


@pytest.mark.parametrize('locale',['sl','it'])
def test_explanation_report_has_single_origin_and_existing_source_link(locale):
    _,_,public=mechanical_explanation();blocks=list(ai.report_blocks(public,locale));texts=[v for k,v in blocks if k!='link']
    assert texts.count(e.LABELS[locale])==1
    assert any(k=='link'and '/families/fixture-family/sources/fixture-source'in v[1]for k,v in blocks)
    assert not any('WARNING'in str(v)for v in texts)
