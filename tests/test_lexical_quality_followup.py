import copy,json,os
from pathlib import Path
import pytest
from core.lexical_focus import focus_profile,editorial
from pilot_backend.lexical import reader

def saved(case):
    root=os.environ.get('KLIKAI_R5_RESULTS')
    if not root:pytest.skip('Retained private r5 fixtures required')
    return json.loads((Path(root)/(case+'.frozen.private.json')).read_text())['bundle']['analysis']['captures'][0]['response']

def view(p):return reader({'locale':'sl','analysis':{'version':'klikai.lexical-collation.v1','captures':[{'response':p}]}})

def test_saved_word_cards_follow_primary_word_and_preserve_automatic_details():
    p=saved('word');before=copy.deepcopy(p);q=focus_profile(p);v=view(q)
    assert p==before and focus_profile(q)==q
    sections={s['title']:s['items']for s in v['sections']}
    assert [r['title']for r in sections['Oblike in pregibanje']]==['lep · pridevnik']
    assert sections['Druge samodejno zapisane analize'][0]['title']=='lep · prislov'
    assert 'Terminologija'not in sections and 'general'not in json.dumps(v)
    assert any(u['kind']=='terminology'and u['selection']['state']=='excluded'for u in q['evidence'])

def test_saved_name_cards_exclude_port_and_preserve_complete_parallel_slot():
    p=focus_profile(saved('variants'));v=view(p)
    assert [s['title']for s in v['sections']]==['Oblike in pregibanje']
    rows=v['sections'][0]['items'];assert len(rows)==1
    assert any(f['title'].startswith('rodilnik · ednina')and f['text']=='Luka / Luke'for f in rows[0]['forms'])
    assert any(u['kind']=='synonyms'for u in p['evidence'])

def test_saved_translation_does_not_display_letter_components():
    v=view(focus_profile(saved('translation')))
    assert [s['title']for s in v['sections']]==['Terminologija']
    assert all('didattica a distanza'in r['title']for r in v['sections'][0]['items'])

def test_phrase_groups_equivalent_observation_without_summing_or_deleting():
    p=saved('phrase');q=focus_profile(p);v=view(q)
    rows=next(s['items']for s in v['sections']if s['title']=='Zapisane besedne zveze')
    assert len(rows)==1 and rows[0]['title']=='lep dan'and'9232'in rows[0]['text']
    chosen=[u for u in q['evidence']if u['kind']=='collocations'and u['selection']['state']=='primary']
    assert len(chosen)==1 and len(chosen[0]['source_memberships'])==2
    assert len(p['evidence'])==len(q['evidence'])
    assert not any(s['title']=='Sopomenke po pomenih'for s in v['sections'])
    # A conflicting measurement is a second observation, not a summed count.
    candidates=[u for u in p['evidence']if u['kind']=='collocations'and u['match_relation']=='inflected_equivalent']
    candidates[-1]['fields']['frequency']+=1
    assert len([u for u in focus_profile(p)['evidence']if u['kind']=='collocations'and u['selection']['state']=='primary'])==2

@pytest.mark.parametrize('raw,field,normalized',[('MANUAL','source_status','reviewed'),('AUTOMATIC','source_status','automatic'),('ssss-automatic','head_status','automatic'),('manual','status','reviewed'),('unknown-value','status','unknown')])
def test_editorial_status_is_typed_and_raw_value_is_kept(raw,field,normalized):
    e=editorial({field:raw});assert e['raw']==raw and e['normalized']==normalized and e['field']==field

def test_other_parallel_forms_fixture_is_not_name_specific():
    p=saved('variants')
    # Clearly labelled fixture reuses only the shape, never claims these are source facts.
    for t,name in zip(p['interpretation']['targets'],['Alfe','Alfa']):t['text']=name
    for part in p['facets']['morphology']['data']:
        part['target']='Alfe'if part['target']=='Luke'else'Alfa'
        for le in (part.get('result')or{}).get('lexemes',[]):
            for slot in le['slots']:
                for v in slot['variants']:
                    if v['form']in {'Luke','Luka'}:v['form']={'Luke':'Alfe','Luka':'Alfa'}[v['form']]
    q=focus_profile(p);assert len(q['interpretation']['selection']['primary_lexeme_ids'])==1

def test_degree_and_definiteness_survive_slot_display():
    p=focus_profile(saved('word'));rows=view(p)['sections'][0]['items'][0]['forms']
    assert any('osnovnik'in r['title']for r in rows)
    assert any('primernik'in r['title']for r in rows)

def checked_profile(case):
    root=os.environ.get('KLIKAI_QUALITY_PROFILES')
    if not root:pytest.skip('Fresh read-only profile fixtures required')
    return json.loads((Path(root)/(case+'.profile.private.json')).read_text())

@pytest.mark.parametrize('case,title',[('acronym','Terminologija'),('letter','Oblike in pregibanje'),('port','Sopomenke po pomenih'),('parallel','Oblike in pregibanje')])
def test_explicit_counterexamples_remain_available(case,title):
    p=checked_profile(case);v=view(p)
    assert any(s['title']==title and s['items']for s in v['sections'])
    if case=='acronym':assert all(s['title']=='Terminologija'for s in v['sections'])
    if case=='port':assert 'pristanišče'in json.dumps(v,ensure_ascii=False)
    if case=='letter':assert any(s['title']=='Zapisane besedne zveze'for s in v['sections'])

def test_actual_phrase_analyses_and_source_slots_remain_distinct():
    p=checked_profile('phrase');u=next(u for u in p['evidence']if u['kind']=='analysis')
    raw=json.dumps(u,ensure_ascii=False)
    assert 'CLASSLA'in raw or 'classla'in raw
    assert 'spaCy'in raw or 'spacy'in raw
    assert 'locative'in raw and 'dative'in raw and 'source_slots'in raw
    v=view(p)
    assert any(s['title']=='Analiza vnesene oblike'for s in v['sections'])
    grouped=next(u for u in p['evidence']if u['kind']=='collocations'and u['selection']['state']=='primary')
    assert all(any(m['record_id']==x['captured_unit_id']for x in p['evidence'])for m in grouped['source_memberships'])
