"""Complete retained cross-class paradigms stay useful and source-faithful."""
import copy
import gzip
import json
from pathlib import Path

import pytest

from core.lexical_focus import focus_profile
from core.lexical_identity import source_occurrence_key
from core.lexical_profile import morphological_records
from core.lexical_planner import plan
from pilot_backend.grammar_presentation import feature_text
from pilot_backend.lexical import reader
from pilot_backend.source_reader import structured
from pilot_backend.collation import _profile_matches_request_plan

ENTRIES=json.loads(gzip.decompress((Path(__file__).parent/
    'fixtures/generic_paradigm_sloleks.json.gz').read_bytes()))['lexemes']
HELDOUT=json.loads(gzip.decompress((Path(__file__).parent/
    'fixtures/generic_paradigm_heldout_sloleks.json.gz').read_bytes()))['lexemes']
ENTRIES+=HELDOUT


def full_cards(entries,locale):
    lexemes=copy.deepcopy(entries)
    for le in lexemes:
        le.update(submitted_form=le['lemma'],matched_by='exact_headword',submitted_analyses=[])
    raw={'lexemes':lexemes}
    p={'interpretation':{'targets':[{'text':lexemes[0]['lemma']}],
        'query_kind':'word','requested_facets':['morphology'],'slot_focus':None,
        'form_choice':{'intent':'dictionary_information'}},
        'facets':{'morphology':{'data':[{'target':lexemes[0]['lemma'],'result':raw}]}},
        'evidence':morphological_records(raw,lexemes[0]['lemma'],None)}
    frozen=focus_profile(p);before=copy.deepcopy(frozen)
    cards=reader({'locale':locale,'analysis':{'version':'klikai.lexical-collation.v1',
        'captures':[{'response':frozen}]}})
    assert frozen==before
    return [row for section in cards['sections']for row in section['items']if row.get('forms')]


@pytest.mark.parametrize('locale',['sl','it'])
def test_all_retained_slots_forms_and_pattern_memberships_survive_with_labels(locale):
    count=0
    for lemma in dict.fromkeys(le['lemma']for le in ENTRIES):
        entries=[le for le in ENTRIES if le['lemma']==lemma]
        cards=full_cards(entries,locale)
        assert len(cards)==len(entries)
        by_key={row['source_occurrence_key']:row for row in cards}
        assert len(by_key)==len(entries)
        for le in entries:
            row=by_key[source_occurrence_key(le['evidence'])]
            assert row['record_id']==le['source_entry_id']
            assert 'patterns'not in row and len(row['forms'])==len(le['slots'])
            for slot,shown in zip(le['slots'],row['forms']):
                count+=1
                assert shown['text']==' / '.join(dict.fromkeys(v['form']for v in slot['variants']))
                assert shown['title'] and 'Vir:'not in shown['title']and'Fonte:'not in shown['title']
                memberships=shown['pattern_memberships']
                assert sum(len(m['forms'])for m in memberships)==sum(len({le['slots'][m['slot']]['variants'][m['variant']]['form']
                    for m in pat['members']if m['slot']==slot['slot_locator']})for pat in le['patterns'])
                assert structured({'kind':'morphology','fields':{'lemma':lemma,'slot':slot}},locale)[0]['text']==shown['title']+'\n'+shown['text']
    assert count==617


def test_verbal_person_form_and_polarity_and_pronoun_clitic_are_distinct():
    entries=[le for le in ENTRIES if le['lemma']=='biti']
    cards=full_cards(entries,'sl')
    assert any('polnopomenski'in row['title']for row in cards)
    assert any('pomožni'in row['title']for row in cards)
    assert {form['title']for row in cards for form in row['forms']
        if 'sedanjik'in form['title']and'1. oseba'in form['title']}
    assert any('nikalna oblika'in form['title']and'nenikalna oblika'not in form['title']
        for row in cards for form in row['forms'])
    personal=next(le for le in ENTRIES if le['lemma']=='jaz'and le['grammatical_type']=='pronoun')
    row=full_cards([personal],'sl')[0]
    assert 'zaimek'in row['title']and'osebni'in row['title']
    assert any('naslonska oblika'in form['title']for form in row['forms'])


def test_yes_is_not_flattened_across_different_source_features_and_unknowns_remain_visible():
    text=feature_text({'definiteness':'yes','negative':'yes','clitic':'yes','animate':'yes'},'sl')
    assert text=='določna oblika · živo · nikalna oblika · naslonska oblika'
    assert feature_text({'new_source_field':'opaque'},'sl')=='Vir: new_source_field=opaque'
    assert feature_text({'person':'unmapped'},'it')=='Fonte: person=unmapped'


def test_heldout_neuter_perfective_and_possessive_classes_use_their_recorded_features():
    for lemma in ['jajce','drevo']:
        le=next(le for le in HELDOUT if le['lemma']==lemma)
        assert le['grammatical_features']=={'type':'common','gender':'neuter'}
        assert 'srednji spol'in full_cards([le],'sl')[0]['title']
    for lemma in ['prispeti','priti']:
        le=next(le for le in HELDOUT if le['lemma']==lemma)
        assert le['grammatical_features']=={'type':'main','aspect':'perfective'}
        row=full_cards([le],'sl')[0]
        assert 'dovršni'in row['title']and len(row['forms'])==25
    le=next(le for le in HELDOUT if le['lemma']=='očetov')
    assert le['grammatical_features']=={'type':'possessive'}
    row=full_cards([le],'sl')[0]
    assert 'svojilni'in row['title']and len(row['forms'])==55


@pytest.mark.parametrize('lemma,grammatical_type,features',[
    ('dober','adjective',{'gender':'feminine','degree':'positive','case':'genitive','number':'singular'}),
    ('jaz','pronoun',{'type':'personal','clitic':'yes','case':'dative','number':'singular'}),
    ('dva','numeral',{'gender':'feminine','case':'genitive','number':'dual'}),
    ('biti','verb',{'type':'auxiliary','vform':'present','person':'first','number':'singular','negative':'no'}),
])
def test_shared_feature_bundle_selects_actual_class_and_complete_matching_source_slots(lemma,grammatical_type,features):
    question='Oblike izraza '+lemma
    request={'version':'klikai.request-plan.v1','original':question,'context':'',
        'target_spans':[{'text':lemma,'start':question.index(lemma),'end':len(question),'basis':'fixture source object'}],
        'requested_facets':['morphology'],'requested_grammar_slots':[],
        'requested_morphology_features':features,'ambiguities':[],
        'explicit_lexemes':[{'text':lemma,'grammatical_type':grammatical_type}]}
    interpretation=plan(question,'','sl',{'raw_engines':{}},request_plan=request)
    assert {k:v for k,v in interpretation['slot_focus'].items()if k!='basis'}==features
    assert interpretation['requested_morphology_features']==features
    assert _profile_matches_request_plan({'interpretation':interpretation},request)
    changed=copy.deepcopy(interpretation);changed['requested_morphology_features']={}
    assert not _profile_matches_request_plan({'interpretation':changed},request)
    lexemes=copy.deepcopy([le for le in ENTRIES if le['lemma']==lemma])
    for le in lexemes:le.update(submitted_form=lemma,submitted_analyses=[],matched_by='exact_headword')
    raw={'lexemes':lexemes}
    units=morphological_records(raw,lemma,interpretation['slot_focus'])
    profile={'interpretation':interpretation,'facets':{'morphology':{'data':[{'target':lemma,'result':raw}]}},'evidence':units}
    focused=focus_profile(profile)
    expected=[]
    for le in lexemes:
        for slot in le['slots']:
            if all(slot['normalized_features'].get(k)==v for k,v in features.items()):
                expected.append((source_occurrence_key(le['evidence']),slot))
    assert expected
    for occurrence,slot in expected:
        assert any(u.get('source_occurrence_key')==occurrence and u['fields'].get('slot')==slot
            and u['selection']['state']=='primary'for u in focused['evidence'])
    cards=reader({'locale':'sl','analysis':{'version':'klikai.lexical-collation.v1','captures':[{'response':focused}]}})
    primary=next(s['items']for s in cards['sections']if s['title']=='Oblike in pregibanje')
    assert all(grammatical_type!='pronoun'or'zaimek'in row['title']for row in primary)
    assert all(grammatical_type!='numeral'or'števnik'in row['title']for row in primary)
    if features.get('type')=='auxiliary':assert all('pomožni'in row['title']for row in primary)


def test_full_paradigm_projection_has_every_complete_slot_of_each_occurrence():
    lexemes=copy.deepcopy([le for le in ENTRIES if le['lemma']=='biti'])
    for le in lexemes:le.update(submitted_form='biti',submitted_analyses=[],matched_by='exact_headword')
    units=morphological_records({'lexemes':lexemes},'biti',None,full_paradigm=True)
    assert len(units)==25+62+2
    for le in lexemes:
        own=[u for u in units if u['source_occurrence_key']==source_occurrence_key(le['evidence'])]
        header=next(u for u in own if 'slot'not in u['fields'])
        assert header['fields']['paradigm_slot_count']==len(le['slots'])
        assert {u['fields']['slot']['slot_locator']for u in own if 'slot'in u['fields']}==set(range(len(le['slots'])))
