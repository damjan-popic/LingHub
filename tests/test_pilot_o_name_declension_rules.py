"""One shared-plan lookup freezes the SP2001 scopes needed for -o names."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from pilot_backend import unified
from pilot_backend.store import digest


FIRST_TEXT=(
    'Nekateri samostalniki, ki gredo po glavnem sklanjatvenem vzorcu '
    'korak, imajo druge imenovalniške končnice, npr. -a, -o, -e, -u: '
    'sluga, Marko, Leo, finale.')
PARADIGM_TEXT=(
    'Zapisi osnovnih oblik (iztočnice in ene oz. dveh spremnih oblik) '
    'nakazujejo cele naglasne tipe. Tako rodilniška končnica nakazuje '
    'naglas v vsej sklanjatvi. Iz oblik bràt bráta sledi brátu.')


def family(question='Kakšen je dajalnik osebnega imena Hugo?',*,case='dative',
           number=None,target='Hugo',context='osebnega imena Hugo'):
    return {'question':question,'locale':'sl','request_plan':{
        'ambiguities':[],'requested_grammar_slots':[
            {'case':case,'number':number}],
        'target_spans':[{'text':target,'context_span':{'text':context}}]}}


def captured(identity,text,*,status='current_reference',slug='sp2001'):
    capture={'source_slug':slug,'resource_id':identity,'title':identity,
        'rights_mode':'restricted','normative_status':status,
        'original_text':text,'snippet':text,'score':100}
    return {'source_slug':slug,'external_id':identity,
        'capture_kind':'resource_record','capture':capture,
        'capture_sha256':digest(capture),'rights_mode':'restricted',
        'context_eligible':True,'score':100,'title':identity}


@pytest.mark.parametrize(('case','number'),[
    ('genitive',None),('genitive','singular'),('dative',None),
    ('dative','singular')])
def test_shared_name_slot_gets_one_class_level_query(case,number):
    value=unified.o_name_declension_rule_query(
        family(case=case,number=number))
    assert value==unified.O_NAME_DECLENSION_RULE_QUERY
    assert 'Hugo' not in value


@pytest.mark.parametrize('changed',[
    {'case':'locative'}, {'number':'plural'}, {'target':'Hug'},
    {'context':'samostalnika avto'},
])
def test_other_frames_do_not_add_a_rule_lookup(changed):
    value=family(**changed)
    assert unified.o_name_declension_rule_query(value)is None
    value=family();value['request_plan']['ambiguities']=['unresolved']
    assert unified.o_name_declension_rule_query(value)is None


def test_only_both_current_sp2001_rule_scopes_are_retained():
    first=captured('first',FIRST_TEXT);paradigm=captured('paradigm',PARADIGM_TEXT)
    assert unified.o_name_declension_rule_capture(first)
    assert unified.o_name_declension_rule_capture(paradigm)
    for changed in (
        captured('proposal',FIRST_TEXT,status='proposal'),
        captured('other-source',FIRST_TEXT,slug='pravopis8'),
        captured('incidental','Marko je osebno ime.'),
    ):
        assert not unified.o_name_declension_rule_capture(changed)


def test_expansion_adds_scoped_rules_once_and_updates_existing_coverage(monkeypatch):
    first=captured('first',FIRST_TEXT);paradigm=captured('paradigm',PARADIGM_TEXT)
    noise=captured('noise','Marko je osebno ime.')
    calls=[]
    def collect(*args,**kwargs):
        calls.append((args[2]['slug'],kwargs['query_override']))
        return [noise,first,paradigm],{
            'status':'ok','returned_count':3,'elapsed_ms':2.0,'search_ms':1.5,
            'query_variants':[kwargs['query_override']],
            'candidates':[{'resource_id':'noise'},{'resource_id':'first'},
                          {'resource_id':'paradigm'}]}
    monkeypatch.setattr(unified,'collect_source',collect)
    definitions=[{'slug':'sp2001','adapter_key':'official_reference_jsonl',
                  'display_name':'SP2001'}]
    found=[deepcopy(first)]
    coverage=[{'family':'sp2001','status':'ok','returned_count':1,
        'per_query_limit':10,'limit':30,'query_variants':['Hugo','Huga','Hugoju'],
        'search_ms':4.0,'elapsed_ms':5.0}]
    with ThreadPoolExecutor(max_workers=1)as pool:
        unified.expand_o_name_declension_rules(
            object(),family(),definitions,found,coverage,None,pool)
    assert calls==[('sp2001',unified.O_NAME_DECLENSION_RULE_QUERY)]
    assert [row['external_id']for row in found]==['first','paradigm']
    status=coverage[0]
    assert status['returned_count']==2 and status['limit']==40
    assert status['declension_rule_expansion']['scoped_returned_count']==2
    assert status['declension_rule_expansion']['added_count']==1
    assert status['query_variants']==[
        'Hugo','Huga','Hugoju',unified.O_NAME_DECLENSION_RULE_QUERY]
    assert status['search_ms']==5.5 and status['elapsed_ms']==7.0


@pytest.mark.parametrize(('extra_status','expected'),[
    ('no_match','ok'),('timeout','partial')])
def test_missing_or_failed_rule_supplement_preserves_surface_status(
        monkeypatch,extra_status,expected):
    surface=captured('surface','Hugo -ja')
    def collect(*args,**kwargs):
        return [],{'status':extra_status,'returned_count':0,'elapsed_ms':2.0,
            'search_ms':1.5,'query_variants':[kwargs['query_override']],
            'candidates':[]}
    monkeypatch.setattr(unified,'collect_source',collect)
    definitions=[{'slug':'sp2001','adapter_key':'official_reference_jsonl',
                  'display_name':'SP2001'}]
    found=[surface]
    coverage=[{'family':'sp2001','status':'ok','returned_count':1,
        'limit':10,'query_variants':['Hugo'],'search_ms':4.0,'elapsed_ms':5.0}]
    with ThreadPoolExecutor(max_workers=1)as pool:
        unified.expand_o_name_declension_rules(
            object(),family(),definitions,found,coverage,None,pool)
    status=coverage[0]
    assert found==[surface] and status['returned_count']==1
    assert status['status']==expected and status['limit']==20
    assert status['declension_rule_expansion']['scoped_returned_count']==0
    assert status['declension_rule_expansion']['added_count']==0


def test_rule_supplement_has_an_explicit_single_request_cap():
    assert unified.O_NAME_DECLENSION_RULE_LOOKUP_LIMIT==1
