"""Useful reader choices from retained full Sloleks paradigms, without inference."""
import copy
import gzip
import json
from pathlib import Path

import pytest

from core.lexical_focus import focus_profile
from core.lexical_identity import source_occurrence_key
from core.lexical_profile import morphological_records, source_record
from core.loris_roles import loris_advice_roles
from core.sloleks_profiles import SloleksProfiles
from pilot_backend.lexical import reader
from pilot_backend.unified import dictionary_lookups, validated_reference_lemmas


ENTRIES = json.loads(gzip.decompress((Path(__file__).parent /
    'fixtures/language_choice_sloleks.json.gz').read_bytes()))['lexemes']


class RetainedReader(SloleksProfiles):
    def __init__(self, entries=ENTRIES):
        self.entries = entries
        self.calls = []

    def lookup(self, surface, limit=4, offset=0):
        self.calls.append((surface, limit, offset))
        found = []
        for original in self.entries:
            le = copy.deepcopy(original)
            analyses = [{'slot_locator': s['slot_locator'],
                'features': s['normalized_features'], 'original_msd': s['original_msd'],
                'variant_locator': v['source_locator'], 'exact_surface': v['form'] == surface}
                for s in le['slots'] for v in s['variants']
                if v['form'].casefold() == surface.casefold()]
            if le['lemma'].casefold() != surface.casefold() and not analyses:
                continue
            le.update(submitted_form=surface, submitted_analyses=analyses,
                matched_by='exact_headword' if le['lemma'] == surface else
                    'folded_headword' if le['lemma'].casefold() == surface.casefold()
                    else 'recorded_form')
            found.append(le)
        found.sort(key=lambda le: (0 if le['lemma'] == surface else
            1 if le['lemma'].casefold() == surface.casefold() else
            2 if le['lemma'][:1] == surface[:1] else 3, int(le['entry_identity'])))
        return {'status': 'matched' if found else 'no_match',
            'submitted_form': surface, 'lexemes': found[offset:offset+limit],
            'truncated': len(found) > offset+limit,
            'next_offset': offset+limit if len(found) > offset+limit else None}


def profile(targets, nominations=None, choice=None, limit=16):
    parts = []; evidence = []; spans = []; start = 0
    for target in targets:
        lemmas = (nominations or {}).get(target, [])
        data = RetainedReader().discover(target, limit, lemmas)
        parts.append({'target': target, 'result': data})
        evidence.extend(morphological_records(data, target, None,(choice or{}).get('construction')))
        spans.append({'text': target, 'start': start, 'end': start+len(target),
            'lemma_candidates': lemmas, 'context_features': []})
        start += len(target)+5
    text = ' ali '.join(targets)
    return {'query': {'text': text, 'context': ''},
        'interpretation': {'original': text, 'targets': spans,
            'query_kind': 'comparison' if len(targets)>1 else 'word',
            'requested_facets': [], 'slot_focus': None,
            'form_choice': choice or {}, 'request_plan': {'original': text,
                'target_spans': spans}},
        'facets': {'morphology': {'data': parts}}, 'evidence': evidence}


def cards(p):
    return reader({'locale': 'sl', 'analysis': {'version': 'klikai.lexical-collation.v1',
        'captures': [{'response': p}]}})


@pytest.mark.parametrize('targets', [['Marka', 'Markota'], ['Markota', 'Marka']])
def test_bare_form_choice_shows_recorded_intended_lemma_not_literal_homographs(targets):
    p = profile(targets, {'Marka': ['Marko'], 'Markota': ['Markota']})
    before = copy.deepcopy(p)
    q = focus_profile(p)
    sections = {s['title']: s['items'] for s in cards(q)['sections']}
    primary = sections['Oblike in pregibanje']
    assert [r['title'] for r in primary] == ['Marko · samostalnik · lastno ime, moški spol']
    assert len(primary[0]['forms']) == 18
    assert any(r['text'] == 'Marka' and 'rodilnik' in r['title'] for r in primary[0]['items'])
    assert any(r['text'] == 'Marka' and 'tožilnik' in r['title'] for r in primary[0]['items'])
    assert q['interpretation']['slot_focus'] is None
    assert q['interpretation']['form_choice_resolution']['conditional'] is True
    # Real separate names and common-noun readings remain fully inspectable.
    alternatives = sections['Druge zapisane možnosti']
    assert len(alternatives) == 8
    assert all(len(r['forms']) == 18 for r in alternatives)
    assert p == before and focus_profile(q) == q


def test_dictionary_intent_preserves_both_gendered_source_occurrences():
    p = focus_profile(profile(['Marka', 'Markota'], {'Marka': ['Marko']},
        {'intent': 'dictionary_information'}))
    rows = cards(p)['sections'][0]['items']
    assert len(rows) == 4
    assert {r['title'].split(' · ')[0] for r in rows} == {'Marka', 'Markota'}
    assert len({r['source_occurrence_key'] for r in rows}) == 4
    assert len({r['record_id'] for r in rows}) == 2  # Publisher IDs are untouched.


@pytest.mark.parametrize('lemma,kind,gender', [
    ('Marka', 'proper_name', 'feminine'),
    ('Markota', 'surname', 'masculine'),
    ('Markota', 'surname', 'feminine'),
    ('marka', 'common_noun', 'feminine'),
    ('Marko', 'surname', 'feminine')])
def test_explicit_lexeme_gender_and_kind_win_over_convenient_correction(lemma, kind, gender):
    p = profile([lemma], {lemma: ['Marko']}, {'explicit_lexemes': [
        {'text': lemma, 'lexical_kind': kind, 'gender': gender}]})
    q = focus_profile(p)
    assert q['interpretation']['form_choice_resolution']['conditional'] is False
    rows = cards(q)['sections'][0]['items']
    assert len(rows) == 1 and rows[0]['title'].startswith(lemma+' · ')
    assert ('ženski spol' if gender == 'feminine' else 'moški spol') in rows[0]['title']


def test_discovery_pages_past_eight_and_keeps_explicit_incompleteness():
    # Synthetic rank pressure around the retained real Marko occurrence.
    marko = next(copy.deepcopy(le) for le in ENTRIES if le['lemma']=='Marko'
        and le['grammatical_features']['gender']=='masculine')
    pressure = []
    for index in range(12):
        le = copy.deepcopy(marko);le['entry_identity'] = str(index+1)
        le['lemma'] = 'Marka';le['evidence']['entry_ordinal'] = 10000+index
        pressure.append(le)
    r = RetainedReader(pressure+[marko])
    result = r.discover('Marka',16)
    assert len(result['lexemes']) == 13 and result['candidate_discovery']['complete']
    assert any(le['lemma']=='Marko' for le in result['lexemes'])
    assert any(offset == 8 for _, _, offset in r.calls)
    bounded = r.discover('Marka',8)
    assert bounded['truncated'] and not bounded['candidate_discovery']['complete']
    # Validated context nominations recover a late candidate without erasing peers.
    nominated = r.discover('Marka',8,['Marko'])
    assert nominated['lexemes'][0]['lemma']=='Marko' and nominated['truncated']


@pytest.mark.parametrize('lemma', ['Luka','Miha','Aljoša','vojvoda'])
def test_masculine_a_classes_keep_full_parallel_paradigms_and_zero_observations(lemma):
    p = focus_profile(profile([lemma], choice={'explicit_lexemes': [
        {'text': lemma, 'gender': 'masculine'}]}))
    row = cards(p)['sections'][0]['items'][0]
    assert 'moški spol' in row['title'] and len(row['forms']) == 18
    assert any(' / ' in f['text'] for f in row['forms'])
    original = next(le for le in ENTRIES if le['lemma']==lemma
        and le['grammatical_features']['gender']=='masculine')
    assert [f['text'].split(' / ') for f in row['forms']] == [
        list(dict.fromkeys(v['form'] for v in s['variants'])) for s in original['slots']]


def test_legitimate_t_extension_is_recorded_under_its_own_lemma():
    p = focus_profile(profile(['Tone','Toneta'], {'Toneta':['Tone']}))
    rows = cards(p)['sections'][0]['items']
    assert any(row['title'].startswith('Tone · ') and any(
        f['text']=='Toneta' for f in row['forms']) for row in rows)


def test_loris_category_roles_show_explanation_and_reference_without_reversing_paronym():
    normative = {'category':'nepravilni','matched_text':'Markota','payload':{
        'Iscete':'Marka','Razlaga':'Daljšanje s -t- je v tem primeru pogovorno.',
        'Povezava':'https://www.smejse.it/reference.pdf'}}
    paronym = {'category':'paronym','matched_text':'lesenka','payload':{
        'Izhodisce':'lesenka','Iscete':'scheda','SteMislili':'kartica',
        'Vslo':'Razlaga pogojev rabe.'}}
    assert loris_advice_roles(paronym)['foreign_equivalent_cue']=='scheda'
    assert loris_advice_roles(paronym)['recommended_replacements']=='kartica'
    assert loris_advice_roles(normative)['foreign_equivalent_cue'] is None
    p = profile(['Markota'])
    p['evidence'].append(source_record('loris','fixture-rule',normative,
        {'resource_id':'loris'},'Markota','whole_expression'))
    rows = next(s['items'] for s in cards(focus_profile(p))['sections'] if s['title']=='LORIS')
    assert rows[0]['text'].endswith(normative['payload']['Razlaga'])
    assert rows[0]['reference_url']==normative['payload']['Povezava']
    p['evidence'][-1]['fields']['payload']={}
    assert not any(s['title']=='LORIS' for s in cards(focus_profile(p))['sections'])


def test_one_complete_paradigm_and_source_membership_metadata_without_duplicate_dump():
    p = focus_profile(profile(['Markota'],choice={'intent':'dictionary_information'}))
    before = copy.deepcopy(p)
    for row in cards(p)['sections'][0]['items']:
        assert len(row['forms'])==18 and 'patterns' not in row
        assert any('Vzorec pregibanja' in d['title'] for d in row['details'])
        assert all(f['pattern_memberships'][0]['forms']==f['text'].split(' / ')
            for f in row['forms'])
    assert p == before


def test_reference_discovery_keeps_occurrence_keys_and_prioritizes_intended_reading():
    p = focus_profile(profile(['Marka','Markota'],{'Marka':['Marko']}))
    capture={'captures':[{'operation':'lexical_profile','response':p}]}
    family={'question':p['query']['text'],'context':'','request_plan':p['interpretation']['request_plan']}
    found=validated_reference_lemmas(capture,family)
    assert found[0]['query']=='Marko'
    hit={'semantic_unit':'dictionary_entry','score_method':'official_reference_exact_headword','published_headword':'Marko'}
    lookup=dictionary_lookups(hit,'Marko',family,found[0])[0]
    assert lookup['version']=='recorded-dictionary-lookup-v1'
    assert lookup['source_entry_ids'] and lookup['source_occurrence_keys']
    assert all(k.startswith('sloleks-occurrence:') for k in lookup['source_occurrence_keys'])


def test_occurrence_identity_includes_archive_shard_ordinal_not_gender_or_native_id():
    female,male=[le for le in ENTRIES if le['lemma']=='Marka']
    assert female['source_entry_id']==male['source_entry_id']
    assert source_occurrence_key(female['evidence'])!=source_occurrence_key(male['evidence'])
    same_gender=copy.deepcopy(female);same_gender['evidence']['entry_ordinal']+=1
    assert source_occurrence_key(female['evidence'])!=source_occurrence_key(same_gender['evidence'])
    assert source_occurrence_key({'resource_id':'sloleks'}) is None


@pytest.mark.parametrize('lemma,primary', [('Marko',True),('Markota',False),('Markot',False)])
def test_normative_advice_follows_the_explicit_source_reading(lemma,primary):
    p=profile(['Marka','Markota'],{'Marka':['Marko']},{'explicit_lexemes':[
        {'text':lemma,'lexical_kind':'surname'if lemma!='Marko'else'proper_name',
         'gender':'masculine'}]})
    p['evidence'].append(source_record('loris','fixture-rule',{
        'category':'nepravilni','matched_text':'Markota','payload':{
            'Iscete':'Marka','Razlaga':'V tem primeru je oblika pogovorna.'}},
        {'resource_id':'loris'},'Markota','whole_expression'))
    q=focus_profile(p);rule=q['evidence'][-1]
    assert (rule['selection']['state']=='primary')is primary
    sections={s['title']:s['items']for s in cards(q)['sections']}
    assert ('LORIS'in sections)is primary
    if not primary:assert any(r['label']=='LORIS'for r in sections['Druge zapisane možnosti'])


def test_metadata_only_focus_is_not_a_request_for_eighteen_compact_rows():
    p=profile(['Marka','Markota'],{'Marka':['Marko']})
    p['interpretation']['slot_focus']={'basis':'unspecified','case':None,'number':None}
    row=cards(focus_profile(p))['sections'][0]['items'][0]
    assert len(row['items'])==4 and len(row['forms'])==18


@pytest.mark.parametrize('case,form', [('nominative','Marko'),('dative','Marku')])
def test_explicit_construction_retains_the_actual_neither_offered_option_slot(case,form):
    choice={'explicit_lexemes':[{'text':'Marko','gender':'masculine'}],
        'construction':{'case_candidates':[case],'number_candidates':['singular']}}
    p=focus_profile(profile(['Marka','Markota'],{'Marka':['Marko']},choice))
    units=[u for u in p['evidence']if u['kind']=='morphology'
        and u['selection']['state']=='primary'and u['fields'].get('slot',{}).get('normalized_features',{}).get('case')==case]
    assert any(form in {v['form']for v in u['fields']['slot']['variants']}for u in units)
    row=cards(p)['sections'][0]['items'][0]
    assert [r['text']for r in row['items']]==[form]
    assert len(row['forms'])==18


def test_noncomparison_named_inflection_uses_explicit_gender_without_editorial_preference():
    p=profile(['Luka'])
    p['interpretation'].pop('form_choice')
    p['interpretation']['explicit_lexemes']=[{'text':'Luka',
        'lexical_kind':'proper_name','gender':'masculine'}]
    p['interpretation']['slot_focus']={'case':'genitive','number':None}
    q=focus_profile(p);sections={s['title']:s['items']for s in cards(q)['sections']}
    row=sections['Oblike in pregibanje'][0]
    assert len(sections['Oblike in pregibanje'])==1 and 'moški spol'in row['title']
    assert row['items'][0]['text']=='Luka / Luke'
    assert len(row['forms'])==18
    assert any('ženski spol'in other['title']and len(other['forms'])==18
        for other in sections['Druge zapisane možnosti'])


def test_related_case_advice_labels_canonical_relation_without_wrong_surface_arrow():
    p=profile(['Marku','Markotu'],{'Marku':['Marko']})
    explanation='Samostalnik spada v prvo moško sklanjatev. Daljšanje s -t- je v tem primeru pogovorno.'
    p['evidence'].append(source_record('loris','retained-rule',{
        'category':'nepravilni','matched_text':'Markotu','trigger':'Markota',
        'payload':{'Iscete':'Marka','Razlaga':explanation}},
        {'resource_id':'loris'},'Markotu','whole_expression'))
    row=next(s['items'][0]for s in cards(focus_profile(p))['sections']if s['title']=='LORIS')
    assert row['text']==explanation
    assert row['details']==[{'title':'Virsko izhodišče','text':'Markota → Marka'}]
