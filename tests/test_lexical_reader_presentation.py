"""Public lexical text is human-readable; exact frozen records remain intact."""
import copy
import pytest
from pilot_backend.lexical import reader


def bundle():
    p={'interpretation':{'selection':{'version':'fixture'},'slot_focus':None},
       'facets':{'morphology':{'data':[]}},'evidence':[
        {'kind':'analysis','record_id':'private-raw-id','target':'lepem dnevu','match_relation':'recorded_target','selection':{'state':'primary'},'provenance':{},'fields':{
            'submitted_expression':'lepem dnevu',
            'engines':{'classla':{'tokens':[{'text':'dnevu','lemma':'dan','feats':{'Case':'Dat'}}]},'spacy':{'tokens':[{'text':'dnevu','lemma':'dan','feats':{'Case':'Loc'}}]}},
            'recorded_forms':{'dnevu':{'recorded_readings':[{'lemma':'dan','selection':'primary','features':{'case':c,'number':'singular'}}for c in ['dative','locative']]}},
            'supported_derivations':[{'fact_class':'supported_application','status':'supported','construction':{'text':'lepem dnevu'},'readings':[{'case':'locative','number':'singular','gender':'masculine'}],'text_sl':'V običajni pridevniško-samostalniški zvezi je »lepem dnevu« v mestniku ednine.'}]}},
        {'kind':'synonyms','record_id':'private-sense-id','target':'lep','match_relation':'headword_candidate','selection':{'state':'primary'},'provenance':{},'fields':{'headword':'lep','head_status':'MANUAL','sense':{'sense_id':'private-id-42','relations':[
            {'target_headword':'čeden','relation_type':'synonym','synonym_type':'core'},
            {'target_headword':'prijeten','relation_type':'synonym','synonym_type':'near'},
            {'target_headword':'grd','relation_type':'antonym'},
            {'target_headword':'drug','relation_type':'opaque-source-type'},]}}},
    ]}
    return {'locale':'sl','analysis':{'version':'klikai.lexical-collation.v1','captures':[{'response':p}]}}


def visible(value):
    if isinstance(value,dict):return '\n'.join(str(v)if k in {'title','text','label','note'} else visible(v)for k,v in value.items()if k!='record_id')
    if isinstance(value,list):return '\n'.join(visible(v)for v in value)
    return ''


def test_derivation_is_primary_and_isolated_readings_stay_separate_without_model_rivalry():
    b=bundle();before=copy.deepcopy(b);cards=reader(b);text=visible(cards)
    assert b==before
    sections={s['title']:s['items']for s in cards['sections']}
    assert 'mestniku ednine'in sections['Razlaga besedne zveze'][0]['text']
    assert 'dajalnik'in sections['Zapisane slovnične možnosti'][0]['text']
    assert 'mestnik'in sections['Zapisane slovnične možnosti'][0]['text']
    assert 'Potreben kontekst'not in sections
    assert not any(x in text for x in ('classla','spacy','private-','MANUAL','opaque-source-type'))


def test_semantic_relations_heading_contains_antonyms_and_human_labels():
    cards=reader(bundle());sections={s['title']:s['items']for s in cards['sections']}
    text=sections['Pomenske povezave'][0]['text']
    assert 'čeden (sopomenka)'in text and 'prijeten (bližnja sopomenka)'in text
    assert 'grd (protipomenka)'in text and 'drug (pomenska povezava)'in text
    assert 'Sopomenke po pomenih'not in sections


def test_old_snapshot_keeps_candidate_ambiguity_without_replaying_engine_disagreement():
    b=bundle();p=b['analysis']['captures'][0]['response'];p['evidence'][0]['fields'].pop('supported_derivations')
    cards=reader(b);sections={s['title']:s['items']for s in cards['sections']}
    assert 'Potreben kontekst'in sections and 'Razlaga besedne zveze'not in sections
    assert 'dajalnik'in visible(cards)and'mestnik'in visible(cards)
    assert 'classla'not in visible(cards)and'spacy'not in visible(cards)


def test_unavailable_morphology_facet_does_not_break_the_saved_answer_reader():
    b=bundle();p=b['analysis']['captures'][0]['response']
    p['facets']['morphology']['data']=None
    cards=reader(b)
    sections={s['title']:s['items']for s in cards['sections']}
    assert 'Oblike in pregibanje'not in sections
    assert 'Razlaga besedne zveze'in sections


def test_italian_cards_localize_derived_text_and_relation_labels():
    b=bundle();b['locale']='it';cards=reader(b);text=visible(cards)
    sections={s['title']:s['items']for s in cards['sections']}
    assert 'Relazioni di significato'in sections
    assert 'grd (contrario)'in text and 'locativo'in text
    assert 'V običajni'not in text and 'MANUAL'not in text


def test_inflection_patterns_have_human_titles_without_changing_source_ids():
    b=bundle();p=b['analysis']['captures'][0]['response'];slot={'slot_locator':0,'normalized_features':{'case':'nominative','number':'singular'},'variants':[{'form':'dan'}]}
    p['facets']['morphology']['data']=[{'result':{'lexemes':[{'source_entry_id':'LE_PRIVATE','lemma':'dan','grammatical_features':{'type':'common','gender':'masculine'},'grammatical_type':'noun','frequency_observations':[],'source_status':'MANUAL','slots':[slot],'patterns':[{'source_pattern_id':'opaque_123','members':[{'slot':0,'variant':0}]}]}]}}]
    before=copy.deepcopy(b);cards=reader(b);text=visible(cards)
    assert 'Vzorec pregibanja 1'in text
    assert 'opaque_123'not in text and 'LE_PRIVATE'not in text and 'MANUAL'not in text
    assert b==before


def no_match_bundle(locale='sl',facets_key='inferred_facets'):
    b=bundle();b['locale']=locale;p=b['analysis']['captures'][0]['response']
    p['interpretation'][facets_key]=['frequency'];p['evidence']=[]
    p['facets']['morphology']={'status':'no_match','data':[
        {'target':'unmatched source expression','status':'no_match','result':{
            'status':'no_match','lexemes':[],'truncated':False,'next_offset':None,
            'provenance':{'resource_id':'sloleks','version':'3.1','status':'READY'}}}]}
    return b


@pytest.mark.parametrize('locale,facets_key',[
    ('sl','requested_facets'),('sl','inferred_facets'),('it','requested_facets')])
def test_frequency_no_match_explanation_uses_frozen_scope_without_fabricating_an_entry(locale,facets_key):
    b=no_match_bundle(locale,facets_key);before=copy.deepcopy(b);cards=reader(b)
    assert b==before and cards['sections']==[]
    assert len(cards['availability_notes'])==1
    note=cards['availability_notes'][0]
    assert 'unmatched source expression'in note and 'Sloleks'in note and '3.1'in note and 'Gigafid'in note
    assert ('ni primerljivega podatka'if locale=='sl'else'non è disponibile un dato comparabile')in note
    assert 'record_id'not in cards and 'frequency_position'not in cards
    # Existing source-backed advice/answer data remains independent and exact.
    other=bundle();other['locale']=locale;b['analysis']['captures'][0]['response']['evidence']=copy.deepcopy(
        other['analysis']['captures'][0]['response']['evidence'])
    assert reader(b)['sections']==reader(other)['sections']


@pytest.mark.parametrize('case',[
    'unrequested','facet_unavailable','facet_partial','facet_unknown','part_unknown',
    'result_unavailable','missing_lexemes','nonempty_lexemes','truncated','truncated_unknown',
    'next_page','pagination_unknown','wrong_source','wrong_edition','source_not_ready','source_unknown'])
def test_frequency_availability_note_never_turns_unknown_partial_or_outage_into_absence(case):
    b=no_match_bundle();p=b['analysis']['captures'][0]['response'];facet=p['facets']['morphology']
    part=facet['data'][0];result=part['result']
    if case=='unrequested':p['interpretation']['inferred_facets']=[]
    elif case=='facet_unavailable':facet['status']='unavailable'
    elif case=='facet_partial':facet['status']='partial'
    elif case=='facet_unknown':facet.pop('status')
    elif case=='part_unknown':part.pop('status')
    elif case=='result_unavailable':result['status']='unavailable'
    elif case=='missing_lexemes':result.pop('lexemes')
    elif case=='nonempty_lexemes':result['lexemes']=[{'lemma':'source match'}]
    elif case=='truncated':result['truncated']=True
    elif case=='truncated_unknown':result.pop('truncated')
    elif case=='next_page':result['next_offset']=10
    elif case=='pagination_unknown':result.pop('next_offset')
    elif case=='wrong_source':result['provenance']['resource_id']='another dictionary'
    elif case=='wrong_edition':result['provenance']['version']='2.0'
    elif case=='source_not_ready':result['provenance']['status']='unavailable'
    elif case=='source_unknown':result.pop('provenance')
    # The deliberately contradictory nonempty fixture has no full source entry.
    if case=='nonempty_lexemes':
        from pilot_backend.lexical import frequency_availability_notes
        assert frequency_availability_notes(p,'sl')==[]
    else:assert 'availability_notes'not in reader(b)
