"""Source-entry/occurrence pages over existing compiled fixture constructors.

Extra homographs, duplicates and measurements are explicit mutations of the
retained test sources, not new linguistic assertions or operational resources.
"""
from copy import deepcopy
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
import pytest

import candidate_app
from core.platform_api import bounded_thesaurus
import test_cjvt_collocations as collocation_source
import test_synonym_index as synonym_source


@pytest.fixture(scope='module')
def collocations():
    fixture=collocation_source.CollocationsTests();fixture.setUp()
    try:
        # Known values lie beyond the old500-row source position; pagination
        # must follow the complete native ordering, including a genuine zero.
        index=fixture.measurement_fixture([(None,None)]*501+[(25,2.5),(50,5.0),(0,0.0)])
        # The constructor fixture intentionally has only acquisition IDs; add
        # catalogue labels in memory for the real capabilities route.
        index.manifest['source'].update(version='2.2',license='CC-BY-SA-4.0')
        yield index
    finally:fixture.tearDown()


@pytest.fixture(scope='module')
def kind_collocations():
    fixture=collocation_source.CollocationsTests();fixture.setUp()
    try:yield fixture.compile()[1]
    finally:fixture.tearDown()


@pytest.fixture(scope='module')
def thesaurus():
    fixture=synonym_source.SynonymIndexTests();fixture.setUp()
    try:
        root=ET.fromstring(synonym_source.FIXTURE)
        template=deepcopy(root[0])
        for number in range(22):
            entry=deepcopy(template)
            entry.find('./head/lexicalUnit').set('id','page-'+str(number).zfill(3))
            for index,sense in enumerate(entry.findall('./body/senseList/sense')):
                sense.set('id',str(1000+2*number+index))
            root.append(entry)
        original=root[0].find('./body/senseList')
        for number in range(19):original.append(ET.Element('sense',{'id':str(5000+number),'dummy':'true'}))
        relations=original[0].find('relatedSenseList')
        for _ in range(101):relations.append(deepcopy(relations[0]))
        fixture.xml.write_text(ET.tostring(root,encoding='unicode'))
        fixture.pin_source()
        yield fixture.compile()[2]
    finally:fixture.doCleanups()


@pytest.fixture
def native_client(monkeypatch,collocations,thesaurus):
    state=candidate_app.app.state
    original=dict(state._state)
    state.resource_files={};state.resource_files_by_kind={}
    state.synonyms=thesaurus;state.collocations=collocations;state.morphology=None
    state.resource_readiness={}
    client=TestClient(candidate_app.app)  # No lifespan/model/resource activation.
    try:yield client
    finally:
        client.close();state._state.clear();state._state.update(original)


@pytest.mark.parametrize('sort,measure',[('freq','frequency'),('logdice','logDice')])
@pytest.mark.parametrize('order,first_ids',[('asc',[1503,1501,1502]),('desc',[1502,1501,1503])])
def test_occurrence_pages_cover_exact_global_order_including_zero_and_null_tail(collocations,sort,measure,order,first_ids):
    legacy=collocations.lookup('otrok',min_freq=None,limit=500,sort=sort,order=order)
    assert 'pagination'not in legacy
    assert legacy==collocations.lookup('otrok',min_freq=None,limit=500,sort=sort,order=order,offset=0,include_pagination=False)
    accumulated=[];offset=0
    while True:
        page=collocations.lookup('otrok',min_freq=None,limit=23,sort=sort,order=order,offset=offset,include_pagination=True)
        rows=page['collocations'];paging=page['pagination']
        assert paging=={'unit':'occurrence','offset':offset,'limit':23,'total':504,
            'source_total':504,'count_returned':len(rows),'has_more':offset+len(rows)<504,
            'next_offset':offset+len(rows)if offset+len(rows)<504 else None}
        accumulated.extend(rows)
        if not paging['has_more']:break
        offset=paging['next_offset']
    assert [row['lexical_unit_id']for row in accumulated[:3]]==first_ids
    assert accumulated[:500]==legacy['collocations']
    assert len(accumulated)==504 and len({row['occurrence_id']for row in accumulated})==504
    assert all(row[measure]is None for row in accumulated[3:])
    assert accumulated[0]['components']==legacy['collocations'][0]['components']
    assert all(row['provenance']['build_id']=='fixture-build'for row in accumulated)
    # Offset alone is additive, even when metadata was not explicitly selected.
    tail=collocations.lookup('otrok',min_freq=None,limit=23,offset=500,sort=sort,order=order)
    assert tail['pagination']['offset']==500 and tail['collocations']==accumulated[500:]


def test_filtered_total_is_not_legacy_source_total_and_empty_pages_are_stable(collocations):
    page=collocations.lookup('otrok',min_freq=25,limit=1,include_pagination=True)
    assert page['count_total']==504
    assert page['pagination']=={'unit':'occurrence','offset':0,'limit':1,'total':2,
        'source_total':504,'count_returned':1,'has_more':True,'next_offset':1}
    last=collocations.lookup('otrok',min_freq=25,limit=1,offset=1,include_pagination=True)
    assert last['collocations'][0]['frequency']==25 and last['pagination']['next_offset']is None
    for options,total,source_total in [({'min_freq':25,'offset':2},2,504),
            ({'min_freq':None,'min_logdice':99},0,504),
            ({'min_freq':None,'offset':2147483647},504,504)]:
        empty=collocations.lookup('otrok',limit=17,include_pagination=True,**options)
        assert empty['collocations']==[]and empty['count_returned']==0
        assert empty['pagination']['total']==total and empty['pagination']['source_total']==source_total
        assert empty['pagination']['has_more']is False and empty['pagination']['next_offset']is None


@pytest.mark.parametrize('kind,total',[('collocation',2),('grammaticalCombination',1),('all',3)])
def test_kind_filters_page_occurrences_without_merging_native_memberships(kind_collocations,kind,total):
    whole=kind_collocations.lookup('otrok',min_freq=None,kind=kind)
    pages=[kind_collocations.lookup('otrok',min_freq=None,kind=kind,limit=1,offset=i,include_pagination=True)
        for i in range(total)]
    assert [row for page in pages for row in page['collocations']]==whole['collocations']
    assert all(page['pagination']['total']==total and page['pagination']['source_total']==total for page in pages)
    if kind!='all':assert all(row['kind']==kind for page in pages for row in page['collocations'])
    assert any(row['frequency']is None for page in pages for row in page['collocations'])


def test_source_entries_not_synonym_words_are_the_page_unit_and_nested_caps_remain_honest(thesaurus):
    original=thesaurus.lookup_evidence('C\u030cas',normalized=True);before=deepcopy(original)
    assert original['count_entries']==25
    # A shared returned value catches accidental in-place truncation/page bleed.
    reader=SimpleNamespace(lookup_evidence=lambda *args,**kwargs:original)
    first=bounded_thesaurus(reader,'C\u030cas',20,offset=0,include_pagination=True)
    second=bounded_thesaurus(reader,'C\u030cas',20,offset=20,include_pagination=True)
    assert original==before
    assert [entry['entry_order']for entry in first['entries']+second['entries']]==[entry['entry_order']for entry in before['entries']]
    assert len(first['entries'])==20 and len(second['entries'])==5
    for page,offset in [(first,0),(second,20)]:
        paging=page['pagination']
        assert paging['unit']=='entry'and paging['offset']==offset and paging['limit']==20 and paging['total']==25
        assert paging['count_returned']==len(page['entries'])
        assert paging['returned_senses']==sum(len(entry['senses'])for entry in page['entries'])
        assert paging['returned_relations']==sum(len(sense['relations'])for entry in page['entries']for sense in entry['senses'])
        assert page['count_senses']==before['count_senses']and page['count_relations']==before['count_relations']
    assert first['pagination']['has_more']is True and first['pagination']['next_offset']==20
    assert second['pagination']['has_more']is False and second['pagination']['next_offset']is None
    assert first['pagination']['truncated_within_entries']is True
    assert second['pagination']['truncated_within_entries']is False
    head=first['entries'][0]
    assert len(head['senses'])==20 and len(head['senses'][0]['relations'])==100
    assert [entry['headword']for entry in first['entries'][:3]]==['Čas','čas','Čas']
    assert head['lexical_units'][0]['lexical_unit_id']=='001'
    assert head['head_status']=='ssss-automatic'and head['senses'][0]['source_attributes']['dummy']=='true'
    first_relation,duplicate=head['senses'][0]['relations'][:2]
    assert [first_relation['source_order'],duplicate['source_order']]==[1,2]
    assert first_relation['target_lexical_unit_id']==duplicate['target_lexical_unit_id']=='002'
    assert first_relation['raw_text']==duplicate['raw_text']=='display text'
    assert first_relation['synonym_score']=='0.500'


def test_thesaurus_legacy_projection_and_past_end_preserve_original_counts(thesaurus):
    old=bounded_thesaurus(thesaurus,'Čas',8,normalized=False)
    explicit=bounded_thesaurus(thesaurus,'Čas',8,normalized=False,offset=0,include_pagination=False)
    assert old==explicit and 'pagination'not in old
    assert len(old['entries'])==8 and old['count_entries']==24
    for offset in [24,2147483647]:
        page=bounded_thesaurus(thesaurus,'Čas',20,normalized=False,offset=offset)
        assert page['entries']==[]and page['count_entries']==24
        assert page['pagination']=={'unit':'entry','offset':offset,'limit':20,'total':24,
            'count_returned':0,'has_more':False,'next_offset':None,'returned_senses':0,
            'returned_relations':0,'truncated_within_entries':False}
    absent=bounded_thesaurus(thesaurus,'not-recorded',8,include_pagination=True)
    assert absent['entries']==[]and absent['pagination']['total']==0
    assert absent['pagination']['has_more']is False and absent['pagination']['next_offset']is None


def test_native_route_default_raw_thesaurus_is_unchanged_and_opt_in_uses_twenty_entries(native_client,thesaurus):
    raw=native_client.get('/v1/resources/thesaurus',params={'lemma':'Čas'})
    assert raw.status_code==200 and raw.json()==thesaurus.lookup_evidence('Čas')
    bounded=native_client.get('/v1/resources/thesaurus',params={'lemma':'Čas','include_pagination':'true'})
    assert bounded.status_code==200 and len(bounded.json()['entries'])==20
    assert bounded.json()['pagination']['limit']==20 and bounded.json()['pagination']['total']==24
    tail=native_client.get('/v1/resources/thesaurus',params={'lemma':'Čas','offset':20})
    assert tail.status_code==200 and len(tail.json()['entries'])==4
    assert tail.json()['pagination']['offset']==20


@pytest.mark.parametrize('route',['/v1/resources/collocations','/v1/resources/thesaurus'])
@pytest.mark.parametrize('params',[{'offset':-1},{'offset':2147483648},{'offset':'1.5'},{'include_pagination':'not-a-bool'}])
def test_native_pagination_parameter_bounds_do_not_change_legacy_error_contract(native_client,route,params):
    assert native_client.get(route,params={'lemma':'otrok',**params}).status_code==422


@pytest.mark.parametrize('route',['/collocations','/collocations/by-lemma'])
def test_legacy_collocation_aliases_keep_unknown_query_and_payload_behavior(native_client,route):
    legacy=native_client.get(route,params={'lemma':'otrok','limit':8})
    unknown=native_client.get(route,params={'lemma':'otrok','limit':8,'offset':-1,'include_pagination':'not-a-bool'})
    assert legacy.status_code==unknown.status_code==200
    assert legacy.json()==unknown.json()and'pagination'not in legacy.json()


@pytest.mark.parametrize('route',['/collocations','/collocations/by-lemma','/v1/resources/collocations','/v1/resources/thesaurus'])
def test_native_empty_and_missing_lemma_contract_remains(native_client,route):
    assert native_client.get(route).status_code==422
    assert native_client.get(route,params={'lemma':'  '}).status_code==400
