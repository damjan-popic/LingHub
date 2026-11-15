"""Delivery selection differs from nomination; originals/qualifiers are immutable."""
from copy import deepcopy
import pytest
from pilot_backend.document_ai_suggestions import context_nomination,optional_context_selection

def candidate(eid='record-1',surface='delovna skupina'):
    return {'evidence_id':eid,'capture':{},'capture_kind':'ordinary_context_unit','rights_mode':'owned',
        'context_eligible':False,'model_context_eligible':True,'source_slug':'native-source',
        'occurrences':[{'start':0,'end':7,'text':'Delovne'}],
        'native_passage':{'source_revision':'r1','tool_kind':'collocations','original_json':{
            'record_id':eid,'fields':{'surface':surface,'headword':'deloven','frequency':7,'logDice':3.2,
                'components':[{'lemma':'deloven'},{'lemma':'skupina'}]}}}}

def choose(records,text,words,findings=()):
    task={'source_text':text,'source_language':'sl'}
    nominations={r['evidence_id']:context_nomination(r,task,words)for r in records}
    before=deepcopy(records)
    selected=optional_context_selection({r['evidence_id']:r for r in records},nominations,set(),findings,text,words)
    assert records==before
    return nominations,selected

def words(text,items):
    return [{'start':a,'end':b,'text':text[a:b],'lemma':lemma,'sentence_id':1}for a,b,lemma in items]

def test_whole_located_construction_without_findings_survives_inflection():
    text='Delovne skupine';w=words(text,[(0,7,'deloven'),(8,15,'skupina')])
    nominated,selected=choose([candidate()],text,w)
    assert nominated['record-1']['eligible']and selected['record-1']['selected']
    assert selected['record-1']['occurrences'][0]['text']==text
    assert selected['record-1']['applicability_not_established']

def test_separated_lemmas_remain_eligible_trace_but_are_not_compulsory_context():
    text='Delovne naloge skupine';w=words(text,[(0,7,'deloven'),(8,14,'naloga'),(15,22,'skupina')])
    nominated,selected=choose([candidate()],text,w)
    assert nominated['record-1']['eligible']and not selected['record-1']['selected']
    assert selected['record-1']['reason']=='eligible_sentence_lemma_candidate_without_located_ordered_construction'

def test_optional_attestation_inside_required_finding_is_not_new_construction_guidance():
    text='Delovne skupine';w=words(text,[(0,7,'deloven'),(8,15,'skupina')])
    finding={'match':{'observed':{'start':0,'end':15,'text':text}}}
    _,selected=choose([candidate()],text,w,[finding])
    assert not selected['record-1']['selected']
    assert selected['record-1']['reason']=='optional_attestation_inside_required_finding_without_independent_construction'

@pytest.mark.parametrize('difference',['same','status','sense','unknown_qualification','frequency'])
def test_same_construction_selection_preserves_distinct_facts_and_qualifications(difference):
    text='Delovne skupine';w=words(text,[(0,7,'deloven'),(8,15,'skupina')])
    first=candidate();second=candidate('record-2');unit=second['native_passage']['original_json']
    if difference=='status':unit['editorial_status']={'raw':'manual'}
    if difference=='sense':unit['fields']['sense_id']='other-native-sense'
    if difference=='unknown_qualification':unit['unknown_native_condition']={'condition':'source-owned'}
    if difference=='frequency':unit['fields']['frequency']=0
    _,selected=choose([first,second],text,w)
    assert selected['record-1']['selected']
    assert selected['record-2']['selected']is(difference!='same')
    if difference=='same':
        assert selected['record-2']['distinct_native_record_retained_in_frozen_trace']
        assert not selected['record-2']['record_equivalence_asserted']

def test_literal_phrase_is_selected_without_analyzer_or_any_findings():
    record=candidate(surface='delovne skupine');text='Delovne skupine'
    nominated,selected=choose([record],text,[])
    assert nominated['record-1']['eligible']and selected['record-1']['selected']


def lexical_options(kind='verb_object'):
    observed={'start':0,'end':15,'text':'Delovne skupine'}
    anchor={'start':8,'end':15,'text':'skupine','upos':'NOUN','deprel':'obj'}
    partner={'start':0,'end':7,'text':'Delovne','upos':'VERB','deprel':'root'}
    if kind=='adjective_noun':anchor.update(upos='ADJ',deprel='amod');partner['upos']='NOUN'
    construction={'kind':kind,'observed':observed,'witnesses':[anchor,partner]}
    return {'options':[{'option_id':'phrase','kind':'collocations','construction':deepcopy(construction),
        'alternatives':[{'evidence_id':'phrase-record'}]},
        {'option_id':'rename','kind':'synonyms','construction':deepcopy(construction),
        'alternatives':[{'evidence_id':'synonym-record'}]}]}


def lexical_records():
    first=candidate('phrase-record',surface='druga zveza')
    second=candidate('synonym-record');second['native_passage']['tool_kind']='synonyms'
    return {r['evidence_id']:r for r in [first,second]}


def test_eligible_object_renaming_does_not_become_compulsory_beside_phrase_guidance():
    records=lexical_records();decisions={eid:{'eligible':False,'reason':'routine_lookup','occurrences':[]}for eid in records}
    selected=optional_context_selection(records,decisions,set(),[],'Delovne skupine',[],lexical_options())
    assert selected['phrase-record']['selected']
    assert not selected['synonym-record']['selected']
    assert selected['synonym-record']['lexical_options'][0]['reason']=='object_head_renaming_adds_no_independent_guidance_to_recorded_phrase'


def test_noun_advice_remains_available_without_independent_phrase_guidance():
    records=lexical_records();records.pop('phrase-record')
    options=lexical_options();options['options']=options['options'][1:]
    decisions={'synonym-record':{'eligible':False,'reason':'routine_lookup','occurrences':[]}}
    selected=optional_context_selection(records,decisions,set(),[],'Delovne skupine',[],options)
    assert selected['synonym-record']['selected']and selected['synonym-record']['applicability_not_established']


def test_independent_modifier_readings_are_not_excluded_with_phrase_alternatives():
    records=lexical_records();decisions={eid:{'eligible':False,'reason':'routine_lookup','occurrences':[]}for eid in records}
    selected=optional_context_selection(records,decisions,set(),[],'Delovne skupine',[],lexical_options('adjective_noun'))
    assert all(s['selected']for s in selected.values())


def test_required_source_is_kept_independently_of_optional_choice_delivery():
    records=lexical_records();decisions={eid:{'eligible':False,'reason':'routine_lookup','occurrences':[]}for eid in records}
    selected=optional_context_selection(records,decisions,{'synonym-record'},[],'Delovne skupine',[],lexical_options())
    assert selected['synonym-record']['selected']and selected['synonym-record']['reason']=='required_finding_source'
