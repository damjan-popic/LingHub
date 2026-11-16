"""Pure WEB packet → CORE effective-view compatibility; no jobs or providers."""
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot_backend import document_ai_suggestions as document_packet
from pilot_backend.document_resource_context import VERSION
from pilot_backend.store import digest


def retained_collocation_packet(core):
    saved=json.loads(gzip.decompress((core/'tests/zrc/fixtures/retained_scoped_relations_v44.json.gz').read_bytes()))
    sources=deepcopy(next(case for case in saved['cases']if case['case']=='COLL-P')['passages'])
    sources=[p for p in sources if p.get('tool_kind')=='collocations'
             and p.get('original_json',{}).get('selection',{}).get('state')=='primary'][:3]
    assert len(sources)==3
    text='; '.join(p['original_json']['fields']['surface']for p in sources)
    original_sha=hashlib.sha256(text.encode()).hexdigest()
    envelopes=[]
    for source in sources:
        phrase=source['original_json']['fields']['surface'];start=text.index(phrase)
        envelopes.append({'occurrence':{'section':0,'start':start,'end':start+len(phrase),
            'text':phrase,'original_sha256':original_sha},'language':'sl',
            'source_passages':[],'tool_passages':[source]})
    context={'version':VERSION,'source_sha256':original_sha,'envelopes':envelopes}
    context['context_sha256']=digest(context)
    bundle={'source_bundle':{'matches':[]},'review_observations':{'ordinary_resources':context}}
    class FrozenRows:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):return self
        def fetchone(self):return (json.dumps(bundle),)
    documents=SimpleNamespace(store=SimpleNamespace(connect=FrozenRows))
    document={'state':'complete','document_id':'offline-retained-collocations','source_sha256':original_sha,'warnings':[],
        'options':{'review_strategy':'evidence_review_v1','source_language':'sl','locale':'sl'},
        'review_manifest':{'manifest_sha256':'4'*64},'sections':[{'index':0,'state':'complete','family_id':'offline-family',
        'location':'text@0:'+str(len(text)),'text':text,'document_result':{'status':'draft','proposed_text':text,'edits':[],
        'review':{'manifest_sha256':'5'*64,'findings':[],'checks':[]}}}]}
    return document_packet.packet(documents,'offline-owner',document),sources


def test_retained_web_document_collocations_keep_legacy_qualified_core_model_view(monkeypatch):
    configured=os.environ.get('KLIKAI_PINNED_CORE_ROOT')
    if not configured:pytest.skip('Select CORE for the paired pure document view contract')
    core=Path(configured).resolve();monkeypatch.syspath_prepend(str(core))
    from tools.zrc_reference import document_ai_suggestions as authoring
    value,sources=retained_collocation_packet(core);before=deepcopy(value)
    view=authoring.model_view(value)
    assert value==before and view['full_packet_sha256']==value['packet_sha256']
    assert len(view['sources'])==len(value['sources'])==3
    assert view['source_representation']['ordinary_qualified_reading_bodies']==sorted(view['sources'])
    decoder_spec=importlib.util.spec_from_file_location('selected_core_document_fixtures',core/'tests/zrc/test_document_ai_suggestions.py')
    decoder=importlib.util.module_from_spec(decoder_spec);decoder_spec.loader.exec_module(decoder)
    expanded=decoder.expand_model_fields(view['sources'],view)
    by_occurrence={p['original_json']['fields']['occurrence_id']:p['original_json']for p in sources}
    for key,source in expanded.items():
        reading=source['source_reading'];identity=dict(zip(reading['identity']['columns'],reading['identity']['values']))
        native=by_occurrence[identity['occurrence_id']];field=native['fields'];parts=reading['presentation']
        assert identity['sense_id']==field['sense_id']and identity['structure_id']==field['structure_id']
        assert field['surface']in parts['rows']and str(field['frequency'])in parts['rows']and str(field['logDice'])in parts['rows']
        assert 'status postopka v viru: '+field['status']in parts['rows']
        assert 'Zajeta poizvedba je zahtevala'in parts['qualifications']
        assert 'Pomenska skupina v viru'not in parts['rows']
        assert reading['scope']=='complete captured variants/conditions; attestation is not contextual applicability'
        assert source['rights_mode']==value['sources'][key]['rights_mode']
        assert source['frozen_audit_ref']=='/sources/'+key
        assert reading['details']['editorial_status']==native['editorial_status']
    # These retained bodies are legacy context, not explicit v6 answer units.
    # No source_scopes are invented and no document/AI setting is enabled.
    assert view['sections'][0]['original_text']==value['sections'][0]['original_text']
    assert view['sections'][0]['evidence_draft']==value['sections'][0]['evidence_draft']
