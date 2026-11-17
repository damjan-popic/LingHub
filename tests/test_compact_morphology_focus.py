"""Synthetic native compact records; no lexicon, provider, or application access."""
import copy
import hashlib
import json

import pytest

from core.lexical_focus import VERSION, focus_profile


def fixture(target='formaprova', forms=None):
    provenance={'resource_id':'synthetic Italian morphology','version':'fixture-v1',
        'source_identifier':'synthetic:it','license':'synthetic',
        'fingerprint':'a'*64,'contextual':False}
    fields={'status':'ok','lang':'it','lemma':target,'normalized':target,
        'forms':[{'form':value,'normalized':value,'msds':['SYNTHETIC'],
                  'pos_families':['SYNTHETIC']} for value in (forms or [target])],
        'matched_by':'exact','msd_filter':None,'truncated':False,
        'contextually_selected':False,'provenance':copy.deepcopy(provenance)}
    unit={'kind':'morphology','entry_id':target,'target':target,
        'match_relation':'recorded_target','fields':fields,'provenance':provenance}
    unit['record_id']='lexical:'+hashlib.sha256(json.dumps(unit,sort_keys=True).encode()).hexdigest()
    analysis={'status':'ok','lang':'it','truncated':False,'items':[{
        'input':target,'status':'ok','truncated':False,'ambiguous':False,
        'candidates':[{'language':'it','lemma':target,'matched_by':'exact'}]}]}
    span={'text':target,'start':0,'end':len(target),'term_language':None,
          'context_features':[],'lemma_candidates':[]}
    return {'interpretation':{'original':target,'targets':[copy.deepcopy(span)],
        'query_kind':'word','requested_facets':[],'slot_focus':None,
        'lookup_languages':['sl','it'],'request_plan':{
            'original':target,'target_spans':[copy.deepcopy(span)],'ambiguities':[]}},
        'facets':{'morphology':{'data':[{'target':target,'result':{'lexemes':[]}}]},
            'italian_morphology':{'status':'matched','data':[{
                'target':target,'analysis':analysis,'paradigms':[copy.deepcopy(fields)]}]}},
        'evidence':[unit]}


def state(profile):
    return focus_profile(profile)['evidence'][0]['selection']['state']


def test_complete_compact_source_membership_survives_empty_rich_inventory():
    p=fixture(forms=['formaprova','formesecondarie'])
    before=copy.deepcopy(p);q=focus_profile(p)
    assert q['evidence'][0]['selection']['state']=='primary'
    assert q['evidence'][0]['selection']['reason']=='recorded_compact_whole_form'
    assert q['evidence'][0]['fields']==before['evidence'][0]['fields']
    assert q['evidence'][0]['provenance']==before['evidence'][0]['provenance']
    assert q['evidence'][0]['record_id']!=before['evidence'][0]['record_id']
    assert q['evidence'][0]['captured_unit_id']==before['evidence'][0]['record_id']
    assert q['interpretation']['selection']['primary_lexeme_ids']==[]
    assert q['interpretation']['selection']['compact_morphology_by_target']=={
        'formaprova':[q['evidence'][0]['record_id']]}
    assert p==before and focus_profile(q)==q
    assert q['interpretation']['selection']['version']==VERSION


@pytest.mark.parametrize('target,recorded',[
    ('čarprova',['carprova']),('Formaprova',['formaprova']),
    ('due parole',['due','parole']),('formaprova estesa',['formaprova'])])
def test_source_head_or_component_does_not_establish_whole_original_form(target,recorded):
    assert state(fixture(target,recorded))=='alternative'


@pytest.mark.parametrize('damage',[
    'partial_paradigm','partial_analysis','partial_candidates','folded_candidate',
    'unknown_language','orphan_unit','different_native_identity','different_provenance'])
def test_partial_or_unjoined_native_observations_remain_alternatives(damage):
    p=fixture();part=p['facets']['italian_morphology']['data'][0]
    if damage=='partial_paradigm':
        p['evidence'][0]['fields']['truncated']=True
        part['paradigms'][0]['truncated']=True
    elif damage=='partial_analysis':part['analysis']['truncated']=True
    elif damage=='partial_candidates':part['analysis']['items'][0]['truncated']=True
    elif damage=='folded_candidate':part['analysis']['items'][0]['candidates'][0]['matched_by']='folded'
    elif damage=='unknown_language':part['analysis']['items'][0]['candidates'][0]['language']='und'
    elif damage=='orphan_unit':part['paradigms']=[]
    elif damage=='different_native_identity':part['analysis']['items'][0]['candidates'][0]['lemma']='altraprova'
    else:p['evidence'][0]['provenance']['version']='another-edition'
    assert state(p)=='alternative'


@pytest.mark.parametrize('language,expected',[('sl','alternative'),('it','primary')])
def test_explicit_expression_language_has_priority(language,expected):
    p=fixture();p['interpretation']['request_plan']['target_spans'][0]['term_language']=language
    assert state(p)==expected


@pytest.mark.parametrize('constraint',['slot','requested_slots','explicit_lexeme','ambiguous_language'])
def test_compact_membership_does_not_satisfy_unproved_grammatical_or_identity_constraints(constraint):
    p=fixture();plan=p['interpretation']
    if constraint=='slot':plan['slot_focus']={'case':'genitive'}
    elif constraint=='requested_slots':plan['request_plan']['requested_grammar_slots']=[{'case':'genitive'}]
    elif constraint=='explicit_lexeme':plan['explicit_lexemes']=[{'text':'formaprova','gender':'feminine'}]
    else:plan['request_plan']['ambiguities']=['term_language_binding_unresolved']
    assert state(p)=='alternative'


def test_translation_task_keeps_its_existing_nontranslation_evidence_exclusion():
    p=fixture();p['interpretation']['requested_facets']=['translation']
    q=focus_profile(p)
    assert q['evidence'][0]['selection']['state']=='excluded'
    assert q['evidence'][0]['selection']['reason']=='translation_whole_target'
    assert q['interpretation']['selection']['compact_morphology_by_target']=={'formaprova':[]}


def test_real_rich_identity_and_compact_identity_can_both_remain_primary():
    p=fixture();target='formaprova'
    rich={'source_entry_id':'synthetic-sl:1','lemma':target,'matched_by':'exact_headword',
        'grammatical_type':'noun','grammatical_features':{'type':'common'},'slots':[]}
    p['facets']['morphology']['data'][0]['result']['lexemes']=[rich]
    p['evidence'].append({'kind':'morphology','entry_id':'synthetic-sl:1',
        'record_id':'synthetic:sl-unit','target':target,'match_relation':'recorded_target',
        'provenance':{'resource_id':'synthetic Sloleks'},'fields':copy.deepcopy(rich)})
    q=focus_profile(p)
    assert [u['selection']['state'] for u in q['evidence']]==['primary','primary']
    assert q['interpretation']['selection']['primary_lexeme_ids']==['synthetic-sl:1']
    assert len(q['interpretation']['selection']['compact_morphology_by_target'][target])==1
    # Selecting source observations does not declare either language preferred.
    assert 'resolved_translation_direction' not in q['interpretation']


def test_an_exact_recorded_inflected_form_keeps_its_native_lemma():
    p=fixture('formeprova',['formaprova','formeprova'])
    fields=p['evidence'][0]['fields'];fields['lemma']='formaprova'
    fields['normalized']='formaprova'
    part=p['facets']['italian_morphology']['data'][0]
    part['paradigms']=[copy.deepcopy(fields)]
    part['analysis']['items'][0]['candidates'][0]['lemma']='formaprova'
    q=focus_profile(p)
    assert q['evidence'][0]['selection']['state']=='primary'
    assert q['evidence'][0]['fields']['lemma']=='formaprova'
    assert q['evidence'][0]['fields']['forms']==fields['forms']
    assert 'form_choice_resolution' not in q['interpretation']


def test_incidental_supporting_lookup_does_not_become_the_requested_expression():
    p=fixture();plan=p['interpretation']
    plan['supporting_lookups']={'targets':copy.deepcopy(plan['targets'])}
    plan['targets']=[]
    assert state(p)=='alternative'
