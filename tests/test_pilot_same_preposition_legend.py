"""One shared-plan lookup freezes the SP2001 normative-marker legend."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from pilot_backend import unified
from pilot_backend.store import digest


LEGEND_TEXT=(
    '1062 Splošne normativne oznake\n\n'
    '● prepovedano beseda ali besedna zveza, ki ne ustreza normi '
    'knjižnega jezika, ima pa knjižno vzporednico.')


def family(*,bases=None,ambiguities=None):
    bases=bases or [
        'shared-preposition comparison alternative; original surface',
        'shared-preposition comparison alternative; original surface']
    return {'question':'Ali je pravilno z otroki ali z otroci?',
        'locale':'sl','request_plan':{
            'ambiguities':ambiguities or [],'target_spans':[
                {'text':'otroki','basis':bases[0]},
                {'text':'otroci','basis':bases[1]}]}}


def captured(identity,text,*,status='current_reference',slug='sp2001'):
    capture={'source_slug':slug,'resource_id':identity,'title':identity,
        'rights_mode':'restricted','normative_status':status,
        'original_text':text,'snippet':text,'score':100}
    return {'source_slug':slug,'external_id':identity,
        'capture_kind':'resource_record','capture':capture,
        'capture_sha256':digest(capture),'rights_mode':'restricted',
        'context_eligible':True,'score':100,'title':identity}


def test_shared_preposition_plan_gets_one_source_owned_legend_query():
    assert (unified.same_preposition_legend_query(family())==
            unified.SAME_PREPOSITION_LEGEND_QUERY)
    assert 'otro' not in unified.SAME_PREPOSITION_LEGEND_QUERY.casefold()


@pytest.mark.parametrize('value',[
    family(bases=['bounded comparison alternative; original surface',
                  'bounded comparison alternative; original surface']),
    family(ambiguities=['coordinated_target_unresolved']),
    {'question':'Ali je pravilno z otroki?','request_plan':{
        'ambiguities':[],'target_spans':[{'text':'otroki','basis':
        'shared-preposition comparison alternative; original surface'}]}},
])
def test_other_or_unresolved_frames_do_not_add_legend_lookup(value):
    assert unified.same_preposition_legend_query(value)is None


def test_only_current_sp2001_full_legend_is_retained():
    assert unified.same_preposition_legend_capture(
        captured('legend',LEGEND_TEXT))
    for changed in (
        captured('proposal',LEGEND_TEXT,status='proposal'),
        captured('other-source',LEGEND_TEXT,slug='pravopis8'),
        captured('incidental','Znak ● je normativna oznaka.'),
        captured('entry','z otróki ●otroci'),
    ):
        assert not unified.same_preposition_legend_capture(changed)


def test_expansion_adds_legend_once_and_updates_existing_coverage(monkeypatch):
    legend=captured('legend',LEGEND_TEXT)
    noise=captured('noise','Znak ● se pojavi v slovarju.')
    calls=[]
    def collect(*args,**kwargs):
        calls.append((args[2]['slug'],kwargs['query_override']))
        return [noise,legend,deepcopy(legend)],{
            'status':'ok','returned_count':3,'elapsed_ms':2.0,
            'search_ms':1.5,'query_variants':[kwargs['query_override']],
            'candidates':[{'resource_id':'noise'},{'resource_id':'legend'}]}
    monkeypatch.setattr(unified,'collect_source',collect)
    definitions=[{'slug':'sp2001','adapter_key':'official_reference_jsonl',
                  'display_name':'SP2001'}]
    entry=captured('entry','otròk: z otróki ●otroci')
    found=[entry]
    coverage=[{'family':'sp2001','status':'ok','returned_count':1,
        'limit':20,'query_variants':['otrok'],'search_ms':4.0,
        'elapsed_ms':5.0}]
    with ThreadPoolExecutor(max_workers=1)as pool:
        unified.expand_same_preposition_legend(
            object(),family(),definitions,found,coverage,None,pool)
    assert calls==[('sp2001',unified.SAME_PREPOSITION_LEGEND_QUERY)]
    assert [row['external_id']for row in found]==['entry','legend']
    status=coverage[0]
    assert status['returned_count']==2 and status['limit']==40
    assert status['same_preposition_legend_expansion'][
        'scoped_returned_count']==2
    assert status['same_preposition_legend_expansion']['added_count']==1
    assert status['query_variants']==[
        'otrok',unified.SAME_PREPOSITION_LEGEND_QUERY]
    assert status['search_ms']==5.5 and status['elapsed_ms']==7.0


@pytest.mark.parametrize(('extra_status','expected'),[
    ('no_match','ok'),('timeout','partial')])
def test_missing_or_failed_legend_preserves_surface_status(
        monkeypatch,extra_status,expected):
    entry=captured('entry','otròk: z otróki ●otroci')
    def collect(*args,**kwargs):
        return [],{'status':extra_status,'returned_count':0,
            'elapsed_ms':2.0,'search_ms':1.5,
            'query_variants':[kwargs['query_override']],'candidates':[]}
    monkeypatch.setattr(unified,'collect_source',collect)
    definitions=[{'slug':'sp2001','adapter_key':'official_reference_jsonl',
                  'display_name':'SP2001'}]
    found=[entry]
    coverage=[{'family':'sp2001','status':'ok','returned_count':1,
        'limit':10,'query_variants':['otrok'],'search_ms':4.0,
        'elapsed_ms':5.0}]
    with ThreadPoolExecutor(max_workers=1)as pool:
        unified.expand_same_preposition_legend(
            object(),family(),definitions,found,coverage,None,pool)
    status=coverage[0]
    assert found==[entry] and status['returned_count']==1
    assert status['status']==expected and status['limit']==20
    assert status['same_preposition_legend_expansion'][
        'scoped_returned_count']==0


def test_legend_supplement_has_an_explicit_single_request_cap():
    assert unified.SAME_PREPOSITION_LEGEND_LOOKUP_LIMIT==1
