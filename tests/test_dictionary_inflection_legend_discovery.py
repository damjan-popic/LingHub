"""A marked requested entry discovers its own source legend exactly once."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import os
import pytest
from pilot_backend import unified

@pytest.fixture
def core(monkeypatch):
    monkeypatch.syspath_prepend(os.environ['KLIKAI_PINNED_CORE_ROOT'])

def entry(target='Marko',marker='°'):
    capture={'source_slug':'sp2001','normative_status':'current_reference',
        'semantic_unit':'dictionary_entry','score_method':'official_reference_exact_headword',
        'published_headword':target,'rights_mode':'owned',
        'dictionary_lookup':{'target':target},
        'original_text':target+' -a, '+marker+'-ta m'}
    return {'source_slug':'sp2001','capture_kind':'resource_record','external_id':'entry:fixture','capture_sha256':'a'*64,'capture':capture}

def family(source='sp2001',target='Marko'):
    return {'question':'Kako se sklanja ime '+target+'?', 'request_plan':{
        'requested_source':source,'requested_edition':'2001',
        'requested_facets':['inflection'],'target_spans':[{'text':target}]}}

def run(monkeypatch,family,found):
    calls=[]
    legend={'source_slug':'sp2001','capture_kind':'resource_record','external_id':'legend:fixture','capture_sha256':'b'*64,
        'capture':{'source_slug':'sp2001','normative_status':'current_reference','rights_mode':'owned',
            'original_text':'° krožec: zaznamuje nepravilno (v knjižnem jeziku)'}}
    def collect(*args,**kwargs):
        calls.append(kwargs['query_override'])
        return [deepcopy(legend)],{'status':'ok','candidates':[],'limit':10,
            'search_ms':1,'elapsed_ms':2,'query_variants':[kwargs['query_override']]}
    monkeypatch.setattr(unified,'collect_source',collect)
    coverage=[{'family':'sp2001','status':'ok','limit':10,'returned_count':1,
        'query_variants':['Marko'],'search_ms':1,'elapsed_ms':2}]
    definitions=[{'slug':'sp2001','adapter_key':'official_reference_jsonl'}]
    with ThreadPoolExecutor(1)as details,ThreadPoolExecutor(1)as pool:
        unified.expand_dictionary_inflection_legend(None,family,definitions,found,coverage,details,pool)
    return calls,coverage

def test_marked_entry_captures_publisher_legend_without_replacing_the_entry(core,monkeypatch):
    found=[entry()];before=deepcopy(found[0])
    calls,coverage=run(monkeypatch,family(),found)
    assert calls==['Splošne normativne oznake']
    assert found[0]==before and len(found)==2
    assert found[1]['capture_sha256']=='b'*64
    assert coverage[0]['dictionary_inflection_legend_expansion']['added_count']==1
    assert coverage[0]['returned_count']==2

def test_discovery_does_not_require_core_installed_in_the_web_process(monkeypatch):
    import builtins
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name.startswith('tools.zrc_reference'):raise ModuleNotFoundError(name)
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    found=[entry()]
    calls,_=run(monkeypatch,family(),found)
    assert calls==['Splošne normativne oznake']and len(found)==2

@pytest.mark.parametrize('change',['other_source','unmarked','different_target','linked_only','other_edition'])
def test_no_extra_lookup_for_unrequested_or_ineligible_entry(core,monkeypatch,change):
    f=family();found=[entry()]
    if change=='other_source':f['request_plan']['requested_source']='epravopis'
    if change=='unmarked':found=[entry(marker='')]
    if change=='different_target':f['request_plan']['target_spans']=[{'text':'Hugo'}]
    if change=='linked_only':found[0]['capture']['rights_mode']='linked_only'
    if change=='other_edition':f['request_plan']['requested_edition']='2002'
    calls,_=run(monkeypatch,f,found)
    assert calls==[] and len(found)==1
