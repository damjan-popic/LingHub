"""Whole-parent source access remains context, never an executable rewrite."""
from copy import deepcopy
import pytest
from pilot_backend import document_transform as doc

PARENT=('Napačne, odsvetovane ali slabe izbire in predlagane rešitve\n\n'
    'nadaljni | ⇨ | nadaljnji\n'
    'v prid manjšine | ⇨ | v prid + dajalnik (komu/čemu)\n'
    'v prid manjšini\n\n'
    's pomočjo\nPodatki so prikazani s pomočjo preglednice.\n'
    'Pri osebi, ki pomaga, je raba ustrezna: s pomočjo tolmača.\n'
    'Predloga: z uporabo, na podlagi.')

def parent(**changes):
    quote='s pomočjo\n'+PARENT.split('s pomočjo\n',1)[1]
    start=PARENT.index(quote)
    row={'evidence_id':'parent','capture_kind':'resource_record','context_eligible':True,'rights_mode':'restricted',
        'source_slug':'handbook','external_id':'actual-parent-shape','capture':{'original_text':PARENT,'language':'sl',
            'edition':'source edition','coverage_note':'language advice; not legal validation',
            'advice_units':[{'source_span':{'char_start':start,'char_end':len(PARENT),'text_sha256':doc.sha_text(quote)},
                'recommendation_force':'recommendation_with_exception','scope_note':'Person-assistance exception remains applicable.'}]}}
    row.update(changes);return row

def task(text,mode='review',language='sl'):
    return {'mode':mode,'source_text':text,'source_language':language,'locale':language}

def term():
    return {'evidence_id':'incidental-term','capture_kind':'terminology_record','context_eligible':True,'rights_mode':'restricted',
        'capture':{'term_entries':[{'lang':'sl','term':'Podatki'}]}}

@pytest.mark.parametrize('text',['Nadaljni potek.','Predlog je v prid manjšine.',
    'Predlog je v prid manjšini.','Podatki so prikazani s pomočjo preglednice.',
    'Ana je govorila s pomočjo tolmača.'])
def test_full_parent_and_correct_forms_remain_available_without_a_special_advice_span(text):
    row=parent();before=deepcopy(row);t=task(text)
    assert doc._documentary_review_occurrences(row,t)
    selection=doc._review_writer_selection(t,[row])
    assert selection['eligible_evidence_ids']==['parent']
    assert selection['scope']=='whole_parent_candidates_not_applicability_or_preference'
    assert row==before # source prose, exceptions, force, edition and qualifications untouched

def test_incidental_term_cannot_exclude_a_complete_relevant_advice_parent():
    row=parent();incidental=term();t=task('Podatki so prikazani s pomočjo preglednice.')
    selection=doc._review_writer_selection(t,[incidental,row])
    assert set(selection['eligible_evidence_ids'])=={'parent','incidental-term'}
    assert doc._writer_source_priority(row,t)<doc._writer_source_priority(incidental,t)
    assert selection['basis']=='document_complete_parent_context_nomination_v2'
    assert 'ne potrjuje uporabnosti' in doc._selection_notice(t,selection)

@pytest.mark.parametrize('damage',['rights','context','language','annotation_hash','fragment','unrelated'])
def test_nomination_does_not_bypass_source_scope_or_word_boundaries(damage):
    row=parent();t=task('Nadaljni potek.')
    if damage=='rights':row['rights_mode']='linked_only'
    elif damage=='context':row['context_eligible']=False
    elif damage=='language':row['capture']['language']='it'
    elif damage=='annotation_hash':row['capture']['advice_units'][0]['source_span']['text_sha256']='0'*64
    elif damage=='fragment':t=task('XNadaljniY')
    else:t=task('Zvezdnato nebo.')
    assert doc._documentary_review_occurrences(row,t)==[]

def test_review_parent_nomination_does_not_expand_translation_scope():
    row=parent();t=task('Nadaljni potek.',mode='translate')
    assert doc._documentary_review_occurrences(row,t)==[]
    assert doc._review_writer_selection(t,[row])is None

def test_common_word_overlap_does_not_crowd_out_a_genuinely_relevant_term():
    row=parent();row['capture']['original_text']='Podatki so uporabni.\n\nDrugo priporočilo.'
    row['capture']['advice_units']=[]
    terminology=term();terminology['capture']['term_entries']=[{'lang':'sl','term':'podnebne spremembe'}]
    t=task('Podatki obravnavajo podnebne spremembe.')
    assert doc._documentary_review_occurrences(row,t)==[{'text':'Podatki','kind':'literal_word'}]
    assert doc._writer_source_priority(terminology,t)<doc._writer_source_priority(row,t)

def test_explicit_term_wins_a_same_length_tie_over_contextual_source_text():
    row=parent();terminology=term();terminology['capture']['term_entries']=[{'lang':'sl','term':'v prid manjšine'}]
    t=task('Predlog je v prid manjšine.')
    assert doc._writer_source_priority(terminology,t)<doc._writer_source_priority(row,t)
