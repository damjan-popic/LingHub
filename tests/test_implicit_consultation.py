"""Actual retained Q3 analysis + mechanical source readers; no live work.

The Q2 contract rows below are mechanical consumer fixtures. They are never
described as native records actually discovered for the old Q3 request.
"""
import asyncio,gzip,json,os,sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

CORE=Path(os.environ['KLIKAI_PINNED_CORE_ROOT'])
sys.path.insert(0,str(CORE/'tests/zrc'))
from test_contextual_lexical_choices import saved
from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
from test_ordinary_contextual_consultation import selected_output
from core import lexical_profile as profile
from pilot_backend.contextual_lexical_scope import supporting_lookups
from tools.zrc_reference.common import value_hash
from tools.zrc_reference.pilot_tool_evidence import validate_capture_set,tool_passages
from tools.zrc_reference.pilot_contextual_consultation import IMPLICIT_VERSION,validate
from tools.zrc_reference.request_interpretation import authoritative_request_plan

def actual():
    return json.loads(gzip.decompress((Path(__file__).parent/'fixtures/implicit_original_Q3_analysis_v1.json.gz').read_bytes()))

def mechanical_analysis(text,words):
    observed=[];cursor=0
    for surface,lemma,upos in words:
        start=text.index(surface,cursor);cursor=start+len(surface)
        observed.append({'id':len(observed),'text':surface,'lemma':lemma,'upos':upos,'feats':{},'start':start,'end':cursor})
    return {'text':text,'status':'ok','raw_engines':{'mechanical':{'analysis':{'model':{'fixture':'mechanical'},'words':observed}}}}

def mock_profile(monkeypatch,value=None,*,adjective_source=False,resources_available=True):
    value=actual()if value is None else value;calls=[];analysis_calls=[]
    async def analyze(*args):analysis_calls.append(True);return deepcopy(value['analysis'])
    monkeypatch.setattr('core.analysis_profiles.analyze',analyze)
    monkeypatch.setattr(profile,'available',lambda request,kind:resources_available and kind in {'synonyms','collocations'})
    native=[deepcopy(s['original_json']['fields'])for s in saved('COLL-P')]
    class Synonyms:
        manifest={'source':saved('SYN-P')[0]['original_json']['provenance']['source'],'fingerprint':'retained-mechanical'}
        def lookup_evidence(self,lemma,**kw):
            calls.append(('synonyms',lemma))
            if adjective_source and lemma=='hiter':
                fields=[s['original_json']['fields']for s in saved('SYN-P')]
                entry={k:deepcopy(fields[0][k])for k in ('headword','head_status','head_metadata')if k in fields[0]}
                entry['senses']=[deepcopy(f['sense'])for f in fields]
                return {'entries':[entry]}
            return {'entries':[]}
    class Collocations:
        manifest={'source':saved('COLL-P')[0]['original_json']['provenance']['source'],'build_id':'retained-mechanical'}
        def get_collocations_for_lemma(self,lemma,**kw):
            calls.append(('collocations',lemma));rows=deepcopy(native)if lemma=='pogodba'else[]
            return {'collocations':rows,'count_returned':len(rows),'count_total':1095 if rows else 0}
    state=NS(sloleks_profiles=None,synonyms=Synonyms(),collocations=Collocations(),lexical_klikai_url=None)
    request=NS(app=NS(state=state))
    req=profile.ProfileRequest(text=value['question'],context=value['context'],lang='sl',request_plan=deepcopy(value['request_plan']))
    response=asyncio.run(profile.make_profile(req,request))
    cap={'operation':'lexical_profile','request':deepcopy(response['query']),'response':response,'response_sha256':value_hash(response),'duration_ms':0}
    capture={'version':'klikai.lexical-collation.v1','plan':{'status':'ready','routing_kind':'automatic_lexical_profile','focus':req.text},
        'captures':[cap],'capture_set_sha256':value_hash([cap])}
    validate_capture_set(capture)
    return value,capture,calls,analysis_calls,request

def test_actual_Q3_nominations_keep_intent_full_context_and_one_distinct_work_bound(monkeypatch):
    value,capture,calls,analysis_calls,_=mock_profile(monkeypatch)
    result=capture['captures'][0]['response'];interpretation=result['interpretation']
    assert interpretation['targets']==[]and interpretation['request_plan']==value['request_plan']
    assert interpretation['request_plan']['consultation']['requested']is False
    assert analysis_calls==[True]
    assert set(calls)=={(kind,lemma)for kind in ('synonyms','collocations')for lemma in ('pripraviti','pogodba','podpisati')}
    assert len(calls)==len(set(calls))==6
    nominees=interpretation['supporting_lookups']['targets']
    assert [t['text']for t in nominees]==['pripravili','pogodbo','podpisali']
    for target in nominees:
        for occurrence in target['supporting_origin']['occurrences']:
            assert value['question'][occurrence['start']:occurrence['end']]==occurrence['text']
    assert result['analysis']==value['analysis'] # names, pronoun, particle, negative auxiliary and raw features unchanged
    assert result['facets']['collocations']['status']=='matched'
    assert result['coverage']['collocations']['attempted']is True
    assert all(u['selection']['state']=='primary'and u['selection']['reason']=='source_head_matches_supporting_observation'for u in result['evidence'])

def test_unavailable_resources_preserve_support_origin_without_claiming_lookup_absence(monkeypatch):
    value,capture,calls,_,_=mock_profile(monkeypatch,resources_available=False)
    result=capture['captures'][0]['response']
    assert calls==[]and result['evidence']==[]
    for kind in ('synonyms','collocations'):
        assert result['facets'][kind]['status']=='unavailable'
        assert all(part['supporting_origin']['analysis_identity']for part in result['facets'][kind]['data'])
    assert result['interpretation']['targets']==[]and result['interpretation']['request_plan']==value['request_plan']

def test_mechanical_contract_record_reaches_useful_optional_ordinary_consumer(monkeypatch):
    value,capture,_,_,_=mock_profile(monkeypatch)
    passages=tool_passages(capture);items=[]
    for i,source in enumerate(passages):
        text=source['text'];eid='E'+str(i+1)
        items.append({**deepcopy(source),'evidence_id':eid,'supports':[{'support_id':eid+'.S1','text':text,'char_start':0,'char_end':len(text),'source_role':'source_passage'}]})
    payload=_plan_runtime_payload(value['question'],items,passages,request_plan=value['request_plan']);payload['tool_evidence']=capture
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    units=[u for u in registry.units if u['payload'].get('version')==IMPLICIT_VERSION]
    assert len(units)==1 and units[0]['consultation_support']is True
    assert registry.required_unit_ids==[] # native focus is not explicit intent or requiredness
    result=selected_output(payload,runtime,registry,units);text=result['answer_md']
    assert value['question']in text
    assert 'podpisati pogodbo'in text and 'Zaimenska navezava »je«'in text and 'nikalno obliko (»nista«)'in text
    assert 'Ločena analiza odvisnosti izvirnika ni na voljo'in text
    assert 'skleniti pogodbo'not in text and 'imeti pogodbo'not in text
    assert 'pripravili pogodbo« ima oporo'not in text and 'popravi'not in text
    assert 'posredujte izraz'not in text.casefold()and result['citations']
    assert all(record['occurrence_basis']=='retained_original_lexical_observation_not_dependency'for record in units[0]['payload']['records'])
    assert not any('head_id'in w for raw in value['analysis']['raw_engines'].values()for w in raw['analysis']['words'])
    output=os.environ.get('KLIKAI_IMPLICIT_RECEIPTS')
    if output:
        Path(output,'mechanical-Q3-consumer.json').write_text(json.dumps({'provenance':{'analysis':value['provenance'],
            'sources':'retained Q2/COLL-P source rows mechanically reused; NOT corrected actual Q3 discovery','selection':'manual offline unit choice','syntax':'absent','actual_calls':0},
            'supporting_lookups':capture['captures'][0]['response']['interpretation']['supporting_lookups'],
            'registry':registry.model_dump(),'answer':result},ensure_ascii=False,indent=2)+'\n')

def test_supporting_sources_precede_incidental_documentary_packing_without_becoming_required(monkeypatch):
    from tools.zrc_reference.pilot_cpu_input import task_order
    value,capture,_,_,_=mock_profile(monkeypatch)
    native=tool_passages(capture)[0];native=dict(native,evidence_id='E2')
    incidental={'evidence_id':'E1','passage_id':'mechanical-incidental','source_id':'mechanical-incidental','source_revision':'a'*64,
        'source_slug':'mechanical-documentary','role':'documentary_capture','title':'Incidental name entry','text':'Unrelated documentary entry.',
        'context_eligible':True,'publication_scope':'resource_record','supports':[]}
    ordered,dispositions,_=task_order([[incidental],[native]],[incidental,native],
        {'kind':'documentary_advice','targets':[],'requested_tool_kinds':[]},query=value['question'],request_plan=value['request_plan'])
    assert ordered[0][0]['passage_id']==native['passage_id']
    assert dispositions[0]['reason']=='original_occurrence_support'and dispositions[0]['covered_targets']==[]
    output=os.environ.get('KLIKAI_IMPLICIT_RECEIPTS')
    if output:Path(output,'admission-dispositions.json').write_text(json.dumps({'provenance':'mechanical retained-source packing control; no actual lookup',
        'actual_calls':0,'explicit_targets':value['request_plan']['target_spans'],'packing_dispositions':dispositions,
        'focus_dispositions':[{'record_id':u['record_id'],'kind':u['kind'],'origin':u['supporting_origin'],'selection':u['selection']}
            for u in capture['captures'][0]['response']['evidence']]},ensure_ascii=False,indent=2)+'\n')

def test_mocked_WEB_freeze_fit_CORE_registry_and_WEB_consumer(monkeypatch,tmp_path):
    from test_contextual_consultation_delivery import paired_delivery
    from test_ordinary_contextual_consultation import observation
    value,tool,calls,analysis_calls,_=mock_profile(monkeypatch)
    f={'question':value['question'],'context':value['context'],'input_language':'sl','locale':'sl','request_plan':value['request_plan']}
    def syntax(text):
        return observation(text,{'scope':'question','start':0,'end':len(text),'text':text},verb='pripraviti')['captures'][0]['response']
    result=paired_delivery(monkeypatch,tmp_path,family_override=f,tool_override=tool,observation_factory=syntax,fit=True)
    text=result['web_result']['answer_md']
    assert value['question']in text and 'Vir Kolokacije izpričuje zvezo »podpisati pogodbo«'in text
    assert 'Analiza napoveduje zvezo »pripravili pogodbo«'in text
    assert 'ne izpričanost te iste zveze v viru ali popravek'in text
    assert len(calls)==6 and analysis_calls==[True]and len(result['mocked_analysis_inputs'])==1
    assert result['registry']['required_unit_ids']==[]
    output=os.environ.get('KLIKAI_IMPLICIT_RECEIPTS')
    if output:Path(output,'mocked-WEB-Q3-delivery.json').write_text(json.dumps({'provenance':{
        'analysis':'actual retained Q3 POS/lemma; source records mechanically reused from retained Q2/COLL-P',
        'dependencies':'manufactured test observation, NOT analyzer output','transports':'mocked','selector':'manual unit IDs','actual_calls':0},
        'mocked_freeze_input_profile':result['payload']['packets']['answer']['input_profile'],
        'mocked_freeze_token_budget':result['payload']['packets']['answer']['token_budget'],
        **{key:result[key]for key in ('request','registry','web_result','mocked_analysis_inputs','manual_unit_selection')}},ensure_ascii=False,indent=2)+'\n')

def test_bare_inflected_adjective_keeps_competing_native_senses_without_dependency_proof(monkeypatch):
    text='Po progi prihaja hitri vlak.'
    value={'question':text,'context':'','request_plan':authoritative_request_plan(text,input_language='sl',locale='sl'),
        'analysis':mechanical_analysis(text,[('Po','po','ADP'),('progi','proga','NOUN'),('prihaja','prihajati','VERB'),('hitri','hiter','ADJ'),('vlak','vlak','NOUN')])}
    _,capture,calls,_,_=mock_profile(monkeypatch,value,adjective_source=True)
    passages=tool_passages(capture);items=[]
    for i,source in enumerate(passages):
        eid='E'+str(i);items.append({**deepcopy(source),'evidence_id':eid,'supports':[{'support_id':eid+'.S1','text':source['text'],'char_start':0,'char_end':len(source['text']),'source_role':'source_passage'}]})
    payload=_plan_runtime_payload(text,items,passages,request_plan=value['request_plan']);payload['tool_evidence']=capture
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    units=[u for u in registry.units if u['payload'].get('version')==IMPLICIT_VERSION]
    result=selected_output(payload,runtime,registry,units)
    assert value['request_plan']['target_spans']==[]and len(calls)<=8
    assert all(w in result['answer_md']for w in ('hitri vlak','ekspresen','železnica','pravo','ssss-automatic'))
    assert 'Analiza napoveduje zvezo'not in result['answer_md'] # no dependency capture supplied
    assert len(result['citations'])==4 and registry.required_unit_ids==[]
    output=os.environ.get('KLIKAI_IMPLICIT_RECEIPTS')
    if output:Path(output,'mechanical-adjective-consumer.json').write_text(json.dumps({'provenance':{
        'analysis':'mechanical inflected adjective control, not fresh analyzer output','sources':'retained SYN-P source groups as a mechanical reader fixture',
        'actual_calls':0,'selection':'manual offline unit choice','syntax':'absent'},'answer':result,'supporting_lookups':capture['captures'][0]['response']['interpretation']['supporting_lookups']},ensure_ascii=False,indent=2)+'\n')

def test_adverb_reader_work_is_attempted_and_no_hit_is_not_a_linguistic_verdict(monkeypatch):
    text='Hitro teče.';value={'question':text,'context':'','request_plan':authoritative_request_plan(text,input_language='sl',locale='sl'),
        'analysis':mechanical_analysis(text,[('Hitro','hitro','ADV'),('teče','teči','VERB')])}
    _,capture,calls,_,_=mock_profile(monkeypatch,value)
    assert ('synonyms','hitro')in calls and('collocations','hitro')in calls
    result=capture['captures'][0]['response']
    assert result['interpretation']['targets']==[]and result['coverage']['synonyms']['attempted']is True
    assert result['facets']['synonyms']['status']=='no_match'and result['evidence']==[]

def test_shared_lookup_window_preserves_unqueried_observations_and_context():
    text='Miza, okno, polje, mesto in cesta ostajajo.'
    plan=authoritative_request_plan(text,input_language='sl',locale='sl')
    analysis=mechanical_analysis(text,[(v,v.lower(),'NOUN')for v in ('Miza','okno','polje','mesto','cesta')]+[('ostajajo','ostajati','VERB')])
    report=supporting_lookups(plan,'sl',analysis)
    assert report is not None and len(report['work_ids'])==report['work_limit']==8
    assert len(report['targets'])==4 and len(report['observed_occurrences'])==6
    assert any(o['reason']=='shared_distinct_work_bound'for o in report['omissions'])
    assert analysis['text']==text and plan['target_spans']==[]

@pytest.mark.parametrize('damage',['occurrence','analysis','rights','source_head'])
def test_independent_carrier_or_capture_rejects_wrong_provenance(monkeypatch,damage):
    value,capture,_,_,_=mock_profile(monkeypatch)
    if damage in {'occurrence','analysis'}:
        bad=deepcopy(capture);response=bad['captures'][0]['response']
        if damage=='occurrence':response['interpretation']['supporting_lookups']['targets'][0]['supporting_origin']['occurrences'][0]['start']+=1
        else:response['analysis']['raw_engines']['classla']['analysis']['words'][4]['lemma']='other'
        bad['captures'][0]['response_sha256']=value_hash(response);bad['capture_set_sha256']=value_hash(bad['captures'])
        with pytest.raises(ValueError):validate_capture_set(bad)
        return
    passages=tool_passages(capture);items=[]
    for i,source in enumerate(passages):
        if damage=='rights':source['rights_mode']='unknown'
        else:source['original_json']['supporting_origin']['lemma']='unrelated'
        eid='E'+str(i);items.append({**deepcopy(source),'evidence_id':eid,'supports':[{'support_id':eid+'.S1','text':source['text'],'char_start':0,'char_end':len(source['text']),'source_role':'source_passage'}]})
    payload=_plan_runtime_payload(value['question'],items,passages,request_plan=value['request_plan']);payload['tool_evidence']=capture
    registry=_plan_runtime()._answer_registry(payload,'answer')
    assert not any(u['payload'].get('version')==IMPLICIT_VERSION for u in registry.units)

@pytest.mark.parametrize('upos,word,lemma',[('ADJ','hitri','hiter'),('ADV','hitro','hitro'),('VERB','delata','delati'),('NOUN','delo','delo')])
def test_resource_classes_are_shared_and_occurrence_specific(upos,word,lemma):
    text=word+' ostaja tukaj.';p=authoritative_request_plan(text,input_language='sl',locale='sl')
    a=mechanical_analysis(text,[(word,lemma,upos),('ostaja','ostajati','VERB'),('tukaj','tukaj','ADV')])
    report=supporting_lookups(p,'sl',a)
    assert report is not None and any(t['text']==word for t in report['targets'])
    assert len(report['work_ids'])<=report['work_limit']==8

@pytest.mark.parametrize('field,value',[('ambiguities',['unresolved']),('requested_edition','2001'),('requested_grammar_slots',[{'case':'dative','number':'singular'}]),('requested_source','sp2001'),('translation_language','it')])
def test_narrow_ambiguous_source_and_translation_restrictions_stay_authoritative(field,value):
    retained=actual();plan=deepcopy(retained['request_plan']);plan[field]=value
    assert supporting_lookups(plan,'sl',retained['analysis'])is None

def test_wrong_scope_ambiguous_reading_and_repeated_occurrence_do_not_expand_work():
    retained=actual();bad=deepcopy(retained['analysis']);bad['text']='wrong'
    assert supporting_lookups(retained['request_plan'],'sl',bad)['targets']==[]
    duplicate=deepcopy(retained['analysis']);duplicate['raw_engines']['other']=deepcopy(duplicate['raw_engines']['classla'])
    duplicate['raw_engines']['other']['analysis']['words'][4]['lemma']='different'
    report=supporting_lookups(retained['request_plan'],'sl',duplicate)
    assert 'pripravili'not in [t['text']for t in report['targets']]
    text='Hitro delata, hitro delata.';plan=authoritative_request_plan(text,input_language='sl',locale='sl')
    a=mechanical_analysis(text,[('Hitro','hitro','ADV'),('delata','delati','VERB'),('hitro','hitro','ADV'),('delata','delati','VERB')])
    report=supporting_lookups(plan,'sl',a)
    assert len(report['targets'])==2 and len(report['work_ids'])==4
    assert all(len(t['supporting_origin']['occurrences'])==2 for t in report['targets'])

def test_initial_profile_is_reused_when_source_advice_adds_no_new_work(monkeypatch):
    from pilot_backend import unified
    value,capture,calls,analyses,_=mock_profile(monkeypatch)
    monkeypatch.setattr('tools.zrc_reference.pilot_answer_plan.source_advice_application_requests',lambda *args:[])
    monkeypatch.setattr(unified.collation,'collect',lambda *a,**kw:pytest.fail('Repeated original profile'))
    f={'question':value['question'],'context':value['context'],'request_plan':value['request_plan']}
    result=unified.supplement_source_advice_morphology(NS(lexical_profiles=True),f,capture,
        [{'source_slug':'cu-slogovni','capture':{'advice_units':[{'mechanical':True}]}}],[])
    assert result is capture and len(calls)==6 and analyses==[True]

def test_compatible_native_lookup_is_shared_but_class_occurrences_are_not_merged(monkeypatch):
    text='Pogodbo pogodbo pripravita.'
    value={'question':text,'context':'','request_plan':authoritative_request_plan(text,input_language='sl',locale='sl'),
        'analysis':mechanical_analysis(text,[('Pogodbo','pogodba','NOUN'),('pogodbo','pogodba','VERB'),('pripravita','pripraviti','VERB')])}
    _,capture,calls,_,_=mock_profile(monkeypatch,value)
    support=capture['captures'][0]['response']['interpretation']['supporting_lookups']
    assert len(calls)==len(set(calls))==4 and len(support['work_ids'])==4
    occurrences=[t for t in support['targets']if t['supporting_origin']['lemma']=='pogodba']
    assert len(occurrences)==2 and {t['supporting_origin']['upos']for t in occurrences}=={'NOUN','VERB'}
    assert occurrences[0]['supporting_origin']['work_ids']==occurrences[1]['supporting_origin']['work_ids']

def test_enrichment_only_does_not_repeat_original_analysis_or_lookup(monkeypatch):
    value,capture,calls,analyses,request=mock_profile(monkeypatch)
    async def extra(*args):return profile.facet('no_match',[]),[]
    monkeypatch.setattr(profile,'source_advice_morphology',extra)
    req=profile.ProfileRequest(text=value['question'],context=value['context'],lang='sl',request_plan=value['request_plan'],
        source_advice_captures=[{'mechanical_source_advice':True}],enrichment_only=True)
    result=asyncio.run(profile.make_profile(req,request))
    assert result['analysis']['status']=='not_attempted'and len(calls)==6 and analyses==[True]
    assert result['interpretation']['targets']==[]and result['interpretation']['request_plan']==value['request_plan']

def test_source_advice_enrichment_adds_real_retained_forms_without_renewing_support_budget(monkeypatch):
    parent=json.loads(gzip.decompress((CORE/'tests/zrc/fixtures/handbook_application_parent_capture.json.gz').read_bytes()))
    lexemes=json.loads(gzip.decompress((CORE/'tests/zrc/fixtures/source_advice_application_sloleks.json.gz').read_bytes()))['lexemes']
    text='Tole bi rad napisal bolj uradno: Sem mnenja, da predlog pomaga.'
    value={'question':text,'context':'','request_plan':authoritative_request_plan(text,input_language='sl',locale='sl'),
        'analysis':mechanical_analysis(text,[('Sem','biti','AUX'),('mnenja','mnenje','NOUN'),('predlog','predlog','NOUN'),('pomaga','pomagati','VERB')])}
    _,base,calls,analyses,request=mock_profile(monkeypatch,value)
    morphology_calls=[]
    class Reader:
        def discover(self,lemma,budget,nominations):
            morphology_calls.append(lemma)
            rows=deepcopy([le for le in lexemes if le['lemma']==lemma])
            for row in rows:
                row.update(submitted_form=lemma,submitted_analyses=[],matched_by='exact_headword',
                    head_source={'fixture':'mechanical lookup envelope over retained complete slots'})
            return {'status':'matched','lexemes':rows,'truncated':False}
    request.app.state.sloleks_profiles=Reader()
    original_calls=list(calls)
    req=profile.ProfileRequest(text=text,context='',lang='sl',request_plan=value['request_plan'],
        source_advice_captures=[parent['capture']],enrichment_only=True)
    response=asyncio.run(profile.make_profile(req,request))
    assert response['evidence']and morphology_calls and calls==original_calls and analyses==[True]
    assert response['analysis']['status']=='not_attempted'
    cap={'operation':'lexical_profile','request':response['query'],'response':response,'response_sha256':value_hash(response),'duration_ms':0}
    validate_capture_set({'version':'klikai.lexical-collation.v1','plan':{'status':'ready','routing_kind':'automatic_lexical_profile','focus':text},
        'captures':[cap],'capture_set_sha256':value_hash([cap])})
    assert response['interpretation']['request_plan']==value['request_plan']
