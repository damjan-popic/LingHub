"""Native collocation ordering, identities, memberships and bounded answers."""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from core.lexical_profile import ProfileRequest,make_profile
import test_cjvt_collocations as native_source
from test_pilot_unified import run_bounded_answer_plan,frozen_matches_from_result


@pytest.fixture
def selected_core(monkeypatch):
    configured=os.environ.get('KLIKAI_PINNED_CORE_ROOT')or os.environ.get('KLIKAI_CORE_REPO')
    if not configured:pytest.skip('Select CORE for the paired collocation contract')
    root=Path(configured).resolve()
    if not(root/'tests/zrc/test_requested_collocations.py').is_file():
        pytest.skip('Selected CORE has no native collocation answer fixture')
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))


@pytest.fixture
def compiled():
    fixture=native_source.CollocationsTests();fixture.setUp()
    try:yield fixture.compile()[1]
    finally:fixture.tearDown()


class Reader:
    """Explicit synthetic query contrasts in the source-derived record shape."""
    def __init__(self,compiled,rows,total=None):
        self.manifest=compiled.manifest;self.rows=deepcopy(rows);self.calls=[];self.total=total
    def get_collocations_for_lemma(self,lemma,**options):
        self.calls.append((lemma,deepcopy(options)))
        rows=[r for r in self.rows if options['kind']=='all'or r['kind']==options['kind']]
        measure='logDice'if options.get('sort')=='logdice'else'frequency'
        rows=sorted(rows,key=lambda r:(r[measure]if r[measure]is not None else-1),reverse=options.get('order','desc')=='desc')
        chosen=deepcopy(rows[:options['limit']])
        return {'lemma':lemma,'lemma_key':lemma.casefold(),'count_total':len(rows)if self.total is None else self.total,
            'count_returned':len(chosen),'collocations':chosen,'filters':deepcopy(options),
            'resource':{'build_id':self.manifest['build_id'],'kind_filter':options['kind']}}


def produce(monkeypatch,reader,question='Kolokacije besede otrok?',morphology=None,analysis_result=None):
    from tools.zrc_reference.request_interpretation import authoritative_request_plan
    async def analysis(*args,**kwargs):
        return analysis_result or {'status':'ok','raw_engines':{},'engines_used':[],'loris_status':'no_match'}
    monkeypatch.setattr('core.analysis_profiles.analyze',analysis)
    plan=authoritative_request_plan(question)
    state=NS(synonyms=None,collocations=reader,sloleks_profiles=morphology,morphology=None)
    value=asyncio.run(make_profile(ProfileRequest(text=question,lang='sl',request_plan=plan),NS(app=NS(state=state))))
    return value,plan


def native_result(value,plan,tmp_path,monkeypatch):
    from tools.zrc_reference.common import value_hash
    from tools.zrc_reference.grounded import packet
    from tools.zrc_reference.pilot_evidence import annotate,source_unit_supports
    from tools.zrc_reference.pilot_tool_evidence import validate_capture_set,tool_passages
    from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload,_generation_payload
    from test_fulltext_grounded import store as core_store
    from test_pilot import runtime as core_runtime,GenerateRequest
    from test_pilot_shared_provider import profiled_runtime
    cap={'operation':'lexical_profile','request':value['query'],'response':value,
         'response_sha256':value_hash(value),'duration_ms':0}
    capture={'version':'klikai.lexical-collation.v1','plan':{'status':'ready',
        'routing_kind':'automatic_lexical_profile','focus':value['query']['text']},
        'captures':[cap],'capture_set_sha256':value_hash([cap])}
    validate_capture_set(capture);sources=tool_passages(capture)
    rows=annotate(source_unit_supports(packet(sources,budget=sum(len(r['text'])for r in sources)),sources))
    prepared=_plan_runtime_payload(plan['original'],rows,sources,request_plan=plan)
    location=tmp_path/'core-store';location.mkdir()
    runtime=core_runtime.__wrapped__(core_store.__wrapped__(location))
    configured,native,_=profiled_runtime(runtime,tmp_path)
    payload=_generation_payload(configured,prepared)
    monkeypatch.setattr(configured,'_unpack',lambda bundle:payload)
    monkeypatch.setattr(configured,'_check_source',lambda current:None)
    native.calls.clear();native.sleeping=True
    result=configured.generate(GenerateRequest(bundle={},tier='answer',model='auto'))
    assert result['execution']['inference_performed']is False
    assert result['provider_attempts']==[]and native.calls==[]
    for client in configured.clients.values():client.post.assert_not_called()
    return result


def units(value):
    return [u for u in value['evidence']if u['kind']=='collocations'and u['selection']['state']=='primary']


def test_real_compiled_reader_preserves_occurrences_measures_and_typed_original_before_core_web(
        selected_core,compiled,monkeypatch,tmp_path):
    before=compiled.lookup('otrok',min_freq=None,limit=500,kind='collocation',sort='freq',order='desc')
    value,plan=produce(monkeypatch,compiled)
    lookup=value['facets']['collocations']['data'][0]['lookups'][0]['result']
    assert lookup['original_result']==before
    assert compiled.lookup('otrok',min_freq=None,kind='all')['count_returned']==3
    selected=units(value);assert len(selected)==2
    assert all(u['fields']['kind']=='collocation'for u in selected)
    fields=next(u['fields']for u in selected if u['fields']['frequency']is None)
    assert fields['logDice']is None and fields['sense_id']=='16268462'
    assert fields['structure_id']==34 and fields['lexical_unit_id']==12250092
    assert [c['text']for c in fields['components']]==['šolski','otroci']
    assert [c['msd']for c in fields['components']]==['Agpmpn','Ncmpn']
    assert fields['provenance']==next(r['provenance']for r in before['collocations']if r['frequency']is None)
    assert all(u['fields']['coverage']['truncated']is False for u in selected)
    result=native_result(value,plan,tmp_path,monkeypatch)
    output=run_bounded_answer_plan(result,frozen_matches_from_result(result),model='auto')
    assert output['generation']['status']=='generated',output
    assert 'šolski otroci'in output['answer_md']and'otroci in mladostniki'in output['answer_md']
    assert '6839'in output['answer_md']and'12.10279'in output['answer_md']
    assert 'ni podano'in output['answer_md']
    assert '| Kolokacija |'not in output['answer_md'],'The ordinary UI renders text, not Markdown tables'
    assert len(output['citations'])==2 and output['execution']['inference_performed']is False


def synthetic_rows(compiled,count):
    base=compiled.lookup('otrok',min_freq=None)['collocations'][0]
    rows=[]
    for number in range(1,count+1):
        r=deepcopy(base);r.update(occurrence_id=number,lexical_unit_id=1000+number,
            surface='otrok vrsta'+str(number),frequency=1000-number,logDice=float(number))
        rows.append(r)
    return rows


def test_native_logdice_order_precedes_answer_cap_and_independent_measures_stay_exact(selected_core,compiled,monkeypatch):
    reader=Reader(compiled,synthetic_rows(compiled,12));before=deepcopy(reader.rows)
    value,_=produce(monkeypatch,reader,'Katere kolokacije besede otrok imajo najvišji logDice?')
    assert reader.rows==before
    assert reader.calls[0][1]=={'min_freq':None,'limit':500,'kind':'collocation','sort':'logdice','order':'desc'}
    selected=units(value);assert len(selected)==8
    assert [u['fields']['occurrence_id']for u in selected]==list(range(12,4,-1))
    assert [u['fields']['coverage']['selection_rank']for u in selected]==list(range(1,9))
    assert selected[0]['fields']['frequency']==988 and selected[0]['fields']['logDice']==12
    assert all(u['fields']['coverage']['selection_truncated']is True for u in selected)
    assert all(u['fields']['coverage']['source_truncated']is False for u in selected)
    result=value['facets']['collocations']['data'][0]['lookups'][0]['result']
    assert result['original_result']['count_returned']==12
    assert result['ranking']['measure']=='logDice'and'source-native ordering'in result['ranking']['scope']


def test_verified_headword_identity_is_selected_before_eight_observation_cap(selected_core,compiled,monkeypatch):
    import test_source_advice_morphology as morphology
    from test_requested_name_paradigm_capture import ENTRIES
    native=[deepcopy(e)for e in ENTRIES if e['lemma']=='otrok']
    assert native;monkeypatch.setattr(morphology,'ENTRIES',native)
    rows=synthetic_rows(compiled,12)
    for r in rows[:9]:r['headword']='Otrok'
    value,_=produce(monkeypatch,Reader(compiled,rows),morphology=morphology.Reader())
    selected=units(value)
    assert [u['fields']['occurrence_id']for u in selected]==[10,11,12]
    assert all(u['fields']['headword']=='otrok'for u in selected)
    assert all(u['fields']['coverage']['eligible_count_in_returned_source']==3 for u in selected)


def test_duplicate_native_memberships_survive_without_summing_or_merging_distinct_measures(selected_core,compiled,monkeypatch):
    rows=synthetic_rows(compiled,1);duplicate=deepcopy(rows[0]);duplicate['occurrence_id']=2
    duplicate['provenance']['source_entry_id']='other-native-entry'
    changed=deepcopy(rows[0]);changed.update(occurrence_id=3,frequency=rows[0]['frequency']+1)
    value,_=produce(monkeypatch,Reader(compiled,[rows[0],duplicate,changed]))
    selected=units(value);assert len(selected)==2
    repeated=next(u for u in selected if u['fields']['frequency']==999)
    assert len(repeated['source_memberships'])==2
    assert {m['entry_id']for m in repeated['source_memberships']}=={'1','2'}
    assert sorted(u['fields']['frequency']for u in selected)==[999,1000]
    assert all(u['fields']['coverage']['source_count_returned']==3 for u in selected)
    assert all(u['fields']['coverage']['eligible_count_in_returned_source']==2 for u in selected)


def test_mixed_spelling_and_collocations_keep_the_verified_source_headword(selected_core,compiled,monkeypatch):
    import test_source_advice_morphology as morphology
    from test_requested_name_paradigm_capture import ENTRIES
    monkeypatch.setattr(morphology,'ENTRIES',[deepcopy(e)for e in ENTRIES if e['lemma']=='otrok'])
    rows=synthetic_rows(compiled,10)
    for row in rows[:8]:row['headword']='Otrok'
    value,plan=produce(monkeypatch,Reader(compiled,rows),
        'Kako se piše otrok in katere kolokacije ima?',morphology=morphology.Reader())
    from tools.zrc_reference.pilot_lexical_relations import plain_listing
    assert 'collocations'in plan['requested_facets']and not plain_listing(plan)
    assert [row['text']for row in value['interpretation']['targets']]==['otrok']
    assert [u['fields']['occurrence_id']for u in units(value)]==[9,10]


@pytest.mark.parametrize('damage',['citation','support','type','identity'])
def test_native_collocation_answer_retains_closed_web_consumer_checks(selected_core,compiled,monkeypatch,tmp_path,damage):
    value,plan=produce(monkeypatch,compiled);result=native_result(value,plan,tmp_path,monkeypatch)
    matches=frozen_matches_from_result(result)
    if damage=='citation':result['citations'][0]['source_revision']='f'*64
    elif damage=='support':result['claims'][0]['support_ids']=['invented']
    elif damage=='type':result['display_blocks'][0]['unit_type']='invented_collocations'
    else:result['model_identity']['model_id']='invented'
    output=run_bounded_answer_plan(result,matches,model='auto')
    assert output['generation']['status']=='failed'and output['evidence']==[]
    assert output['generation']['stage']==('provider_identity'if damage=='identity'else'citation_validation')


def test_ordinary_enrichment_keeps_its_existing_query_bound_and_no_new_coverage(selected_core,compiled,monkeypatch):
    reader=Reader(compiled,synthetic_rows(compiled,12))
    value,_=produce(monkeypatch,reader,'otrok')
    assert reader.calls[0][1]=={'min_freq':None,'limit':8,'kind':'all'}
    data=value['facets']['collocations']['data'][0]['lookups'][0]['result']
    assert 'original_result'not in data and len(data['collocations'])==8
    assert all('coverage'not in u['fields']for u in value['evidence']if u['kind']=='collocations')


class RecordedForms:
    """Actual fixture paradigms, including identities with a submitted inflection."""
    def __init__(self,entries):
        self.entries=deepcopy(entries)
        self.manifest={'source_archive_sha256':entries[0]['evidence']['source_archive_sha256']}
    def lookup(self,surface,*args):
        rows=[]
        for original in self.entries:
            analyses=[{'slot_locator':slot['slot_locator'],'features':slot['normalized_features'],
                'original_msd':slot['original_msd'],'variant_locator':variant['source_locator'],
                'exact_surface':True}for slot in original['slots']for variant in slot['variants']
                if variant['form']==surface]
            if original['lemma']!=surface and not analyses:continue
            le=deepcopy(original)
            le.update(submitted_form=surface,matched_by='exact_headword'if le['lemma']==surface
                else'recorded_form',submitted_analyses=analyses)
            rows.append(le)
        return {'status':'matched'if rows else'no_match','lexemes':rows,
            'submitted_form':surface,'truncated':False,'next_offset':None}
    discover=lookup


def test_verified_primary_lemma_scopes_native_inventory_before_capture(selected_core,compiled,monkeypatch):
    from test_requested_name_paradigm_capture import ENTRIES
    native=[e for e in ENTRIES if e['lemma']in {'Marko','Marka'}]
    morphology=RecordedForms(native);reader=Reader(compiled,synthetic_rows(compiled,12))
    for row in reader.rows:row['headword']='Marko'
    value,_=produce(monkeypatch,reader,'Katere so kolokacije besede Marko?',morphology=morphology)
    # Marko is also an attested inflection of Marka. Preserve that morphology,
    # while the already source-verified requested identity owns the inventory.
    assert {le['lemma']for part in value['facets']['morphology']['data']
        for le in part['result']['lexemes']}=={'Marko','Marka'}
    assert [lemma for lemma,_ in reader.calls]==['Marko']
    part=value['facets']['collocations']['data'][0]
    assert part['candidate_selection']['considered_candidates']==['Marko','Marka']
    assert part['candidate_selection']['basis']=='verified_primary_source_lemmas'
    assert part['lookups'][0]['result']['original_result']['collocations']==reader.rows
    assert len(units(value))==8


def test_inflected_surface_retains_verified_different_source_lemma(selected_core,compiled,monkeypatch):
    from test_requested_name_paradigm_capture import ENTRIES
    morphology=RecordedForms([e for e in ENTRIES if e['lemma']=='otrok'])
    reader=Reader(compiled,synthetic_rows(compiled,3))
    value,_=produce(monkeypatch,reader,'Katere so kolokacije besede otroka?',morphology=morphology)
    assert [target['text']for target in value['interpretation']['targets']]==['otroka']
    assert [lemma for lemma,_ in reader.calls]==['otrok']
    part=value['facets']['collocations']['data'][0]
    assert part['candidate_selection']['retained_candidates']==['otrok']
    assert part['lookups'][0]['result']['original_result']['collocations']==reader.rows
    assert len(units(value))==3


def test_native_normalized_keys_share_one_window_and_ordinary_wording_collects(
        selected_core,compiled,monkeypatch):
    from pilot_backend.collation import collect
    from pilot_backend.store import canonical
    question='Katere so značilne besedne zveze z besedo otrok?'
    start=question.index('otrok')
    # Analyzer nominations vary in case; only the native lookup's declared
    # normalization coalesces calls. It does not invent a morphology identity.
    analysis={'status':'ok','engines_used':['first','second'],'loris_status':'no_match',
        'raw_engines':{name:{'analysis':{'words':[{'lemma':lemma,'start':start,
            'end':start+5,'feats':{},'upos':'NOUN'}]}}
            for name,lemma in [('first','otrok'),('second','OTROK')]}}
    rows=synthetic_rows(compiled,12);rows[0]['headword']='Otrok'
    reader=Reader(compiled,rows)
    value,plan=produce(monkeypatch,reader,question,analysis_result=analysis)
    assert [lemma for lemma,_ in reader.calls]==['otrok']
    lookup=value['facets']['collocations']['data'][0]['lookups'][0]
    assert lookup['query_memberships']==['otrok','OTROK']
    assert lookup['result']['original_result']['collocations']==reader.rows
    assert lookup['result']['original_result']['collocations'][0]['headword']=='Otrok'
    class Gateway:
        lexical_profiles=True;linghub_url='http://fixture.invalid';calls=[]
        def request(self,path,payload,base):
            self.calls.append((path,payload))
            if path=='/v1/capabilities':return {'lexical_profile':{'available':True}}
            assert path=='/v1/lexical/profile'and payload['request_plan']==plan
            return value
    gateway=Gateway()
    captured=collect(gateway,{'question':question,'request_plan':plan,
        'input_language':'sl'},automatic=True)
    assert [path for path,_ in gateway.calls]==['/v1/capabilities','/v1/lexical/profile']
    assert captured['captures'][0]['response']==value
    assert len(canonical(captured).encode())<1536*1024
