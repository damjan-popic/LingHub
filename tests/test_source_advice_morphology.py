"""Pre-freeze applications retain native paradigms and the original request."""
import asyncio
from copy import deepcopy
import gzip
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from core.lexical_identity import source_occurrence_key
from core.lexical_profile import ProfileRequest, make_profile
from pilot_backend import collation, lexical, unified

QUESTION='Tole bi rad napisal bolj uradno: Sem mnenja, da je predlog primeren.'
ENTRIES=json.loads(gzip.decompress((Path(__file__).parent/
    'fixtures/source_advice_application_sloleks.json.gz').read_bytes()))['lexemes']


@pytest.fixture
def core(monkeypatch):
    root=Path(os.environ['KLIKAI_PINNED_CORE_ROOT'])
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    return root


@pytest.fixture
def advice_capture(core):
    from test_handbook_advice_answer_plan import committed_advice_item
    item,source=committed_advice_item('biti-mnenja-po-mojem-po-moje')
    return dict(item,resource_id=item['span']['external_id'],
        original_text=item['text'],source_revision=source['source_revision'])


class Reader:
    manifest={'source_archive_sha256':ENTRIES[0]['evidence']['source_archive_sha256']}
    def __init__(self):self.calls=[]
    def lookup(self,surface,*args):
        self.calls.append(surface)
        rows=deepcopy([le for le in ENTRIES if le['lemma']==surface])
        for le in rows:
            le.update(submitted_form=surface,matched_by='exact_headword',
                submitted_analyses=[{'slot_locator':s['slot_locator'],
                    'features':s['normalized_features'],'original_msd':s['original_msd'],
                    'variant_locator':v['source_locator'],'exact_surface':True}
                    for s in le['slots']for v in s['variants']if v['form']==surface])
        return {'status':'matched'if rows else'no_match','lexemes':rows,
            'submitted_form':surface,'truncated':False,'next_offset':None}
    def discover(self,surface,*args):return self.lookup(surface)


def profile(monkeypatch,question,captures,request_plan=None):
    async def analysis(*args,**kwargs):
        return {'status':'ok','raw_engines':{},'engines_used':[],'loris_status':'no_match'}
    monkeypatch.setattr('core.analysis_profiles.analyze',analysis)
    reader=Reader()
    request=NS(app=NS(state=NS(sloleks_profiles=reader,synonyms=None,
        collocations=None,morphology=None)))
    req=ProfileRequest(text=question,lang='sl',profile='klikai_classla_primary_v1',request_plan=request_plan,
        **({'source_advice_captures':captures}if captures is not None else{}))
    return asyncio.run(make_profile(req,request)),reader


def test_bound_source_nomination_keeps_complete_actual_slots_and_original_plan(
        core,monkeypatch,advice_capture):
    from tools.zrc_reference.request_interpretation import response
    from tools.zrc_reference.pilot_tool_evidence import validate_capture_set,tool_passages
    plan=response(QUESTION,'','sl','sl')['request_plan'];before=deepcopy(plan)
    value,_=profile(monkeypatch,QUESTION,[advice_capture],plan)
    original,_=profile(monkeypatch,QUESTION,None,plan)
    def view(value):
        return {'locale':'sl','analysis':{'version':'klikai.lexical-collation.v1',
            'captures':[{'response':value}]}}
    assert lexical.reader(view(value))==lexical.reader(view(original))
    assert lexical.overview(view(value))==lexical.overview(view(original))
    assert value['interpretation']['request_plan']==plan==before
    assert [row['text']for row in value['interpretation']['targets']]==[
        row['text']for row in plan['target_spans']]
    assert collation._profile_matches_request_plan(value,plan)
    supplement=value['facets']['source_advice_morphology']['data']
    assert {part['request']['lemma']for part in supplement}=={'biti','meniti'}
    units=[u for u in value['evidence']if u.get('application_support')]
    assert {u['fields']['lemma']for u in units}=={'biti','meniti'}
    for part in supplement:
        wanted=part['request'];features=wanted['features']
        for le in part['result']['lexemes']:
            original=next(row for row in ENTRIES if source_occurrence_key(row['evidence'])==source_occurrence_key(le['evidence']))
            assert le['slots']==original['slots'] and le['patterns']==original['patterns']
            expected=[s for s in le['slots']if all(s['normalized_features'].get(k)==v for k,v in features.items())]
            own=[u for u in units if u['source_occurrence_key']==source_occurrence_key(le['evidence'])]
            if not expected:
                assert not own
                continue
            header,=[u for u in own if 'slot'not in u['fields']]
            assert header['entry_id']==le['source_entry_id']
            assert header['fields']['paradigm_slot_count']==len(le['slots'])
            assert {u['fields']['slot']['slot_locator']:u['fields']['slot']for u in own if 'slot'in u['fields']}=={s['slot_locator']:s for s in expected}
            assert all(u['application_support']==wanted['basis'] and
                u['selection']['state']=='primary'for u in own)
    cap={'operation':'lexical_profile','request':value['query'],'response':value,
        'response_sha256':unified.digest(value),'duration_ms':0}
    capture={'version':'klikai.lexical-collation.v1','plan':{'status':'ready',
        'routing_kind':'automatic_lexical_profile','focus':QUESTION},'captures':[cap],
        'capture_set_sha256':unified.digest([cap])}
    validate_capture_set(capture)
    passages=tool_passages(capture)
    assert {p['original_json']['fields']['lemma']for p in passages
        if p['original_json'].get('application_support')}=={'biti','meniti'}
    assert all(p['context_eligible']for p in passages
        if p['original_json'].get('application_support'))


@pytest.mark.parametrize('damage',['source','span','unrelated_question'])
def test_unbound_source_cannot_nominate_application_forms(core,monkeypatch,advice_capture,damage):
    question=QUESTION
    if damage=='source':advice_capture['source_slug']='other-source'
    elif damage=='span':advice_capture['advice_units'][0]['source_span']['text_sha256']='f'*64
    else:question='Kakšen je rodilnik imena Hugo?'
    value,reader=profile(monkeypatch,question,[advice_capture])
    assert not any(u.get('application_support')for u in value['evidence'])
    assert 'biti'not in reader.calls and 'meniti'not in reader.calls


def test_ordinary_profile_keeps_the_existing_query_shape(core,monkeypatch):
    value,_=profile(monkeypatch,'Hugo',None)
    assert 'source_advice_captures'not in value['query']
    assert 'source_advice_morphology'not in value['facets']


def test_collector_supplements_once_before_freeze_without_retargeting(
        core,monkeypatch,advice_capture):
    from tools.zrc_reference.request_interpretation import response
    from tools.zrc_reference.pilot_tool_evidence import validate_capture_set
    plan=response(QUESTION,'','sl','sl')['request_plan']
    class Gateway:
        lexical_profiles=True
        linghub_url='synthetic-private-profile'
        def __init__(self):self.requests=[]
        def request(self,path,payload,base):
            self.requests.append((path,deepcopy(payload)))
            if path=='/v1/capabilities':return {'lexical_profile':{'available':True}}
            assert path=='/v1/lexical/profile'
            assert payload['text']==QUESTION and payload['context']==''
            assert payload['request_plan']==plan
            return profile(monkeypatch,QUESTION,payload.get('source_advice_captures'),plan)[0]
    gateway=Gateway();family={'question':QUESTION,'input_language':'sl','request_plan':plan}
    initial=collation.collect(gateway,family,automatic=True);before=deepcopy(initial)
    found=[{'source_slug':'cu-slogovni','capture':advice_capture}]*2;coverage=[]
    result=unified.supplement_source_advice_morphology(gateway,family,initial,found,coverage)
    requests=[payload for path,payload in gateway.requests if path=='/v1/lexical/profile']
    assert len(requests)==2 and len(requests[-1]['source_advice_captures'])==1
    assert initial==before and family['request_plan']==plan
    assert result['captures'][0]['response']['interpretation']==initial['captures'][0]['response']['interpretation']
    assert coverage[0]['status']=='ok'and coverage[0]['returned_count']>0
    validate_capture_set(result)


def test_actual_profile_slots_produce_the_applied_core_answer_through_web(
        core,monkeypatch,advice_capture):
    from test_source_advice_applications import opinion_sources
    from tools.zrc_reference.request_interpretation import authoritative_request_plan
    from tools.zrc_reference.pilot_tool_evidence import tool_passages
    from tools.zrc_reference.pilot_evidence import annotate
    from tools.zrc_reference.grounded import packet
    from tools.zrc_reference.pilot_cpu_input import prepare,flatten
    from tools.zrc_reference.pilot_runtime import PilotRuntime
    plan=authoritative_request_plan(QUESTION,'',input_language='sl',locale='sl')
    value,_=profile(monkeypatch,QUESTION,[advice_capture],plan)
    cap={'operation':'lexical_profile','request':value['query'],'response':value,
        'response_sha256':unified.digest(value),'duration_ms':0}
    capture={'version':'klikai.lexical-collation.v1','plan':{'status':'ready',
        'routing_kind':'automatic_lexical_profile','focus':QUESTION},'captures':[cap],
        'capture_set_sha256':unified.digest([cap])}
    rows=[opinion_sources(QUESTION)[0],*tool_passages(capture)]
    groups,info=prepare(annotate(packet(rows,budget=sum(len(row['text'])for row in rows))),
        rows,'answer',query=QUESTION,request_plan=plan)
    selected=flatten(groups)
    payload={'question':QUESTION,'context':'','locale':'sl','query_plan':plan,'passages':rows,
        'packets':{'answer':dict(info,evidence=selected,model_evidence=selected,
            evidence_packet_hash=unified.digest(selected),model_input_hash=unified.digest(selected))}}
    runtime=PilotRuntime.__new__(PilotRuntime);runtime.key=b'web-application-fixture-key'
    registry=runtime._answer_registry(payload,'answer')
    selector=runtime._single_required_answer_plan(registry,'sl')
    assert selector is not None
    result=runtime._process_fresh_answer_plan(selector,registry,payload=payload,tier='answer')
    matches=[]
    for evidence in result['evidence']:
        match={'evidence_id':unified.evidence_id(evidence),'source_slug':evidence['source_slug'],
            'source':{'locator':deepcopy(evidence['locator'])}}
        if evidence['source_slug']=='cu-slogovni':
            match['capture_sha256']='c'*64
        match['_citation_binding']=unified.frozen_citation_binding(match,
            dict(evidence,citation_id=unified.evidence_id(evidence)))
        matches.append(match)
    body={'contract':unified.CONTRACT,'locale':'sl','matches':matches,
        'gateway_bundle':{'synthetic':True},'retrieval':{'status':'complete'}}
    body['snapshot_sha256']=unified.digest(body)
    def consume(result):
        return unified.generate(NS(request=lambda *args:result),body,'short','qwen')
    output=consume(deepcopy(result))
    assert output['generation']['status']=='generated'
    assert output['answer_md'].startswith('Predlagani zapis: »Menim, da je predlog primeren.«')
    assert len(output['citations'])>=3
    assert {row['source_key']for row in output['citations']}=={'cu-slogovni','sloleks'}
    forged=deepcopy(result)
    next(row for row in forged['citations']if row['source_key']=='sloleks')['source_revision']='f'*64
    rejected=consume(forged)
    assert rejected['generation']['error_code']=='unknown_citation'
    assert rejected['generation']['stage']=='citation_validation'


def test_failed_supplement_preserves_the_independent_original_capture(monkeypatch):
    initial={'version':'klikai.lexical-collation.v1','captures':[]};before=deepcopy(initial)
    def unavailable(*args,**kwargs):raise unified.Failure('source_timeout',503)
    monkeypatch.setattr(collation,'collect',unavailable)
    coverage=[]
    result=unified.supplement_source_advice_morphology(NS(lexical_profiles=True),{},initial,
        [{'source_slug':'cu-slogovni','capture':{'advice_units':[{}]}}],coverage)
    assert result==initial==before
    assert coverage[0]['status']=='timeout'and coverage[0]['returned_count']==0


def test_unrelated_advice_keeps_complete_native_paradigms_without_duplicate_capture(
        core,monkeypatch,advice_capture):
    from tools.zrc_reference.request_interpretation import response
    from tools.zrc_reference.pilot_tool_evidence import validate_capture_set
    question='Kako se sprega glagol biti?'
    plan=response(question,'','sl','sl')['request_plan']
    class Gateway:
        lexical_profiles=True
        linghub_url='synthetic-private-profile'
        def __init__(self):self.requests=[];self.responses=[]
        def request(self,path,payload,base):
            if path=='/v1/capabilities':return {'lexical_profile':{'available':True}}
            assert path=='/v1/lexical/profile'
            self.requests.append(deepcopy(payload))
            value=profile(monkeypatch,question,payload.get('source_advice_captures'),plan)[0]
            self.responses.append(deepcopy(value))
            return value
    gateway=Gateway();family={'question':question,'input_language':'sl','request_plan':plan}
    initial=collation.collect(gateway,family,automatic=True);before=deepcopy(initial)
    found=[{'source_slug':'cu-slogovni','capture':advice_capture}]
    documentary_before=deepcopy(found);coverage=[]
    result=unified.supplement_source_advice_morphology(gateway,family,initial,found,coverage)
    assert len(gateway.requests)==2
    supplemented=gateway.responses[-1]
    assert supplemented['query']['source_advice_captures']==[advice_capture]
    assert supplemented['facets']['source_advice_morphology']['status']=='not_applicable'
    assert not any(u.get('application_support')for u in supplemented['evidence'])
    assert result is initial and result==before
    assert found==documentary_before
    assert coverage[0]['status']=='no_match'and coverage[0]['returned_count']==0
    assert 'source_advice_captures'not in result['captures'][0]['request']
    raw=result['captures'][0]['response']['facets']['morphology']['data'][0]['result']
    expected=[le for le in ENTRIES if le['lemma']=='biti']
    assert len(raw['lexemes'])==len(expected)==2
    for le in raw['lexemes']:
        native=next(source for source in expected if source_occurrence_key(source['evidence'])==source_occurrence_key(le['evidence']))
        assert le['slots']==native['slots']and le['patterns']==native['patterns']
    assert sum(len(le['slots'])for le in raw['lexemes'])==87
    validate_capture_set(result)
