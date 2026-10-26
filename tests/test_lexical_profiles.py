import copy
import pytest
from core.analysis_profiles import align
from core.lexical_planner import plan
from core.sloleks_profiles import measures
import xml.etree.ElementTree as ET

def test_measure_zero_missing_and_readings_not_aggregated():
 node=ET.fromstring('<x><measureList><measure type="frequency" source="test">0</measure><measure type="frequency" source="test"/></measureList></x>')
 rows=measures(node,'surface_form_in_recorded_reading','test')
 assert rows[0]['value']==0 and rows[1]['value']is None
 assert len(rows)==2 and all(r['scope']=='surface_form_in_recorded_reading'for r in rows)

def test_original_target_spans_and_no_identifier_enrichment():
 a={'raw_engines':{}}
 assert plan('lesenka','','und',a)['targets'][0]['text']=='lesenka'
 assert not plan('https://example.test/123','','und',a)['targets']
 p=plan('brez Luke ali brez Luka','','sl',a)
 assert [t['text']for t in p['targets']]==['Luke','Luka']
 assert p['slot_focus']['case']=='genitive'
 assert 'orthography'in plan('Novo mesto ali Novo Mesto?','','sl',a)['requested_facets']
 assert plan('Kdo je Luka?','','sl',a)['slot_focus']['case']=='nominative'

def test_portable_plan_distinguishes_narrow_case_from_broad_inflection():
 narrow=plan('Kako se glasi rodilnik imena Hugo?','','sl',{'raw_engines':{}})
 assert narrow['requested_facets']==['morphology']
 assert narrow['request_plan']['requested_facets']==['genitive']
 broad=plan('Kako sklanjamo ime Hugo?','','sl',{'raw_engines':{}})
 assert broad['slot_focus']is None
 assert broad['request_plan']['requested_facets']==['inflection']


@pytest.mark.parametrize(('question','target','case'),[
 ('Kako se sklanja ime Hugo v rodilniku?','Hugo','genitive'),
 ('Kako pregibamo priimek Novak v dajalniku?','Novak','dative'),
])
def test_postfixed_case_inflection_binds_one_named_target_and_ordered_slot(
        question,target,case):
 request=plan(question,'','sl',{'raw_engines':{}})['request_plan']
 assert request['target_spans']==[{
  'text':target,'start':question.index(target),
  'end':question.index(target)+len(target),
  'basis':'natural lexical object; whole original span'}]
 assert request['requested_grammar_slots']==[{'case':case,'number':None}]
 assert request['requested_facets']==[case]
 assert request['ambiguities']==[]


def test_postfixed_case_inflection_does_not_collapse_coordinated_names():
 question='Kako se sklanja ime Hugo in Miha v rodilniku?'
 request=plan(question,'','sl',{'raw_engines':{}})['request_plan']
 assert request['target_spans']==[]
 assert request['requested_grammar_slots']==[]
 assert request['ambiguities']==['grammar_target_binding_unresolved']

@pytest.mark.parametrize('language,text,case',[
 ('sl','Imenovalnik imena Hugo?','nominative'),
 ('sl','Rodilnik imena Hugo?','genitive'),
 ('sl','Dajalnik imena Hugo?','dative'),
 ('sl','Tožilnik imena Hugo?','accusative'),
 ('sl','Mestnik imena Hugo?','locative'),
 ('sl','Orodnik imena Hugo?','instrumental'),
 ('it','Nominativo del nome Hugo?','nominative'),
 ('it','Genitivo del nome Hugo?','genitive'),
 ('it','Dativo del nome Hugo?','dative'),
 ('it','Accusativo del nome Hugo?','accusative'),
 ('it','Locativo del nome Hugo?','locative'),
 ('it','Strumentale del nome Hugo?','instrumental'),
])
def test_portable_plan_preserves_explicit_case_in_both_languages(language,text,case):
 request=plan(text,'',language,{'raw_engines':{}})['request_plan']
 assert request['requested_facets']==[case]
 assert request['requested_grammar_slots']==[{'case':case,'number':None}]

@pytest.mark.parametrize('text,number',[
 ('Rodilnik ednine besede Hugo?','singular'),
 ('Rodilnik dvojine besede Hugo?','dual'),
 ('Rodilnik množine besede Hugo?','plural'),
 ('Genitivo singolare del nome Hugo?','singular'),
 ('Genitivo plurale del nome Hugo?','plural'),
])
def test_portable_plan_serializes_only_explicit_grammatical_number(text,number):
 request=plan(text,'','sl'if'Rodilnik'in text else'it',{'raw_engines':{}})['request_plan']
 assert request['requested_facets']==['genitive',number]
 assert request['requested_grammar_slots']==[{'case':'genitive','number':number}]


@pytest.mark.parametrize('language,text,slots,facets',[
 ('sl','Rodilnik ednine in dajalnik množine imena Hugo?',[
  {'case':'genitive','number':'singular'},
  {'case':'dative','number':'plural'}],
  ['genitive','dative','singular','plural']),
 ('it','Genitivo singolare e dativo plurale del nome Hugo?',[
  {'case':'genitive','number':'singular'},
  {'case':'dative','number':'plural'}],
  ['genitive','dative','singular','plural']),
 ('sl','Kako se glasita dajalnik množine in rodilnik ednine imena Hugo?',[
  {'case':'dative','number':'plural'},
  {'case':'genitive','number':'singular'}],
  ['dative','genitive','plural','singular']),
 ('it','Quali sono il dativo plurale e il genitivo singolare del nome Hugo?',[
  {'case':'dative','number':'plural'},
  {'case':'genitive','number':'singular'}],
  ['dative','genitive','plural','singular']),
 ('sl','Rodilnik ednine ter dajalnik množine imena Hugo?',[
  {'case':'genitive','number':'singular'},
  {'case':'dative','number':'plural'}],
  ['genitive','dative','singular','plural']),
 ('it','Genitivo singolare ed dativo plurale del nome Hugo?',[
  {'case':'genitive','number':'singular'},
  {'case':'dative','number':'plural'}],
  ['genitive','dative','singular','plural']),
])
def test_ordered_grammar_slots_preserve_case_number_pairing_and_exact_target(
        language,text,slots,facets):
 request=plan(text,'',language,{'raw_engines':{}})['request_plan']
 assert request['requested_grammar_slots']==slots
 assert request['requested_facets']==facets
 assert request['ambiguities']==[]
 assert [row['text']for row in request['target_spans']]==['Hugo']
 row=request['target_spans'][0]
 assert text[row['start']:row['end']]=='Hugo'


@pytest.mark.parametrize('language,text,slots',[
 ('sl','Rodilnik ednine in množine imena Hugo?',[{'case':'genitive','number':'singular'},
  {'case':'genitive','number':'plural'}]),
 ('it','Genitivo singolare e plurale del nome Hugo?',[{'case':'genitive','number':'singular'},
  {'case':'genitive','number':'plural'}]),
])
def test_repeated_case_number_shorthand_expands_ordered_slots(language,text,slots):
 request=plan(text,'',language,{'raw_engines':{}})['request_plan']
 assert request['requested_grammar_slots']==slots
 assert [row['text']for row in request['target_spans']]==['Hugo']


@pytest.mark.parametrize('language,text',[
 ('sl','Rodilnik ali dajalnik imena Hugo?'),
 ('sl','Rodilnik ednine ali množine imena Hugo?'),
 ('it','Genitivo o dativo del nome Hugo?'),
 ('it','Genitivo singolare oppure plurale del nome Hugo?'),
])
def test_disjunctive_grammar_connectors_never_become_cumulative_slots(language,text):
 request=plan(text,'',language,{'raw_engines':{}})['request_plan']
 assert request['requested_grammar_slots']==[]
 assert [row['text']for row in request['target_spans']]==['Hugo']
 assert 'grammar_slot_pairing_unresolved'in request['ambiguities']


@pytest.mark.parametrize('language,text',[
 ('sl','Kako se sklanja ime Hugo?'),
 ('it','Come si declina il nome Hugo?'),
])
def test_broad_inflection_has_no_invented_grammar_slot(language,text):
 request=plan(text,'',language,{'raw_engines':{}})['request_plan']
 assert request['requested_grammar_slots']==[]
 assert request['requested_facets']==['inflection']


@pytest.mark.parametrize('language,text,ambiguity',[
 ('sl','Rodilnik ednine dajalnik množine imena Hugo?',
  'grammar_slot_pairing_unresolved'),
 ('it','Genitivo singolare dativo plurale del nome Hugo?',
  'grammar_slot_pairing_unresolved'),
 ('sl','Rodilnik in dajalnik ednine in množine imena Hugo?',
  'grammar_slot_pairing_unresolved'),
 ('it','Genitivo e dativo singolare e plurale del nome Hugo?',
  'grammar_slot_pairing_unresolved'),
 ('sl','Rodilnik ednine in dajalnik množine imena Hugo in Miha?',
  'grammar_target_binding_unresolved'),
 ('it','Genitivo singolare e dativo plurale del nome Hugo e Miha?',
  'grammar_target_binding_unresolved'),
])
def test_unparseable_pairing_or_ambiguous_target_fails_closed(
        language,text,ambiguity):
 request=plan(text,'',language,{'raw_engines':{}})['request_plan']
 assert request['requested_grammar_slots']==[]
 if ambiguity=='grammar_target_binding_unresolved':
  assert request['target_spans']==[]
 else:
  assert [row['text']for row in request['target_spans']]==['Hugo']
 assert ambiguity in request['ambiguities']


@pytest.mark.parametrize('language,text',[
 ('sl','Kako se glasi rodilnik imena?'),
 ('it','Qual è il genitivo del nome?'),
])
def test_grammar_role_label_without_lexical_target_fails_closed(language,text):
 request=plan(text,'',language,{'raw_engines':{}})['request_plan']
 assert request['target_spans']==[]
 assert request['requested_grammar_slots']==[]
 assert request['ambiguities']==['grammar_target_binding_unresolved']

@pytest.mark.parametrize('language,text,facets',[
 ('sl','Dajalnik ali rodilnik imena Hugo?',['dative','genitive']),
 ('sl','Rodilnik ali dajalnik imena Hugo?',['genitive','dative']),
 ('it','Dativo oppure genitivo del nome Hugo?',['dative','genitive']),
 ('it','Genitivo oppure dativo del nome Hugo?',['genitive','dative']),
 ('sl','Kako se glasi dajalnik ali rodilnik imena Hugo?',['dative','genitive']),
 ('it','Qual è il dativo oppure genitivo del nome Hugo?',['dative','genitive']),
 ('it','Quali sono il dativo e il genitivo del nome Hugo?',['dative','genitive']),
 ('sl','Rodilnik množine ali ednine besede Hugo?',['genitive','plural','singular']),
 ('sl','Rodilnik ednine ali množine besede Hugo?',['genitive','singular','plural']),
 ('sl','Kako se glasi rodilnik množine ali ednine besede Hugo?',['genitive','plural','singular']),
 ('it','Genitivo plurale oppure singolare del nome Hugo?',['genitive','plural','singular']),
 ('it','Genitivo singolare oppure plurale del nome Hugo?',['genitive','singular','plural']),
])
def test_portable_plan_retains_every_ambiguous_case_and_number(
        language,text,facets):
 result=plan(text,'',language,{'raw_engines':{}})
 assert result['request_plan']['requested_facets']==facets
 assert [target['text']for target in result['targets']]==['Hugo']

@pytest.mark.parametrize('text',[
 'Come si declina il nome Hugo?',
 'Come si flette il nome Hugo?',
])
def test_italian_broad_inflection_uses_current_request_only(text):
 history='Prima abbiamo discusso il genitivo plurale e un esempio.'
 result=plan(text,history,'it',{'raw_engines':{}})
 assert result['request_plan']['requested_facets']==['inflection']
 assert [target['text']for target in result['targets']]==['Hugo']
 assert result['request_plan']['target_spans'][0].get('span_scope')is None


@pytest.mark.parametrize('text,context',[
 ('Come si declina il nome sloveno Hugo?','nome sloveno Hugo'),
 ('Come si declina il cognome francese Hugo?','cognome francese Hugo'),
])
def test_italian_named_object_descriptors_retain_context_but_sign_exact_name(text,context):
 result=plan(text,'','it',{'raw_engines':{}})
 assert [row['text']for row in result['targets']]==['Hugo']
 target=result['targets'][0]
 assert text[target['start']:target['end']]=='Hugo'
 descriptor=target['context_span']
 assert descriptor['text']==context
 assert text[descriptor['start']:descriptor['end']]==context
 assert result['request_plan']['target_spans'][0]['context_span']==descriptor


def test_italian_ui_matrix_comparison_splits_tasks_and_keeps_each_named_context():
 text=('Come si declina il nome sloveno Hugo e in che cosa differisce '
       'dal cognome francese Hugo?')
 result=plan(text,'','it',{'raw_engines':{}})
 assert result['query_kind']=='comparison'
 assert [row['text']for row in result['targets']]==['Hugo','Hugo']
 assert [row['context_span']['text']for row in result['targets']]==[
  'nome sloveno Hugo','cognome francese Hugo']
 assert all(text[row['start']:row['end']]=='Hugo'for row in result['targets'])
 assert all(text[row['context_span']['start']:row['context_span']['end']]
            ==row['context_span']['text']for row in result['targets'])
 assert result['request_plan']['requested_facets']==['inflection']
 assert all('e in che cosa'not in row['text']
            for row in result['request_plan']['target_spans'])


@pytest.mark.parametrize('language,question,context,facets,target',[
 ('sl','Kako se glasi rodilnik imena Hugo?','Popravek: množina.',
  ['genitive','plural'],'Hugo'),
 ('sl','Kako se glasi rodilnik imena Hugo?','Popravek: dajalnik.',
  ['dative'],'Hugo'),
 ('sl','Kako se glasi rodilnik imena Hugo?','Popravek: dajalnik, ime Miha.',
  ['dative'],'Miha'),
 ('it','Genitivo del nome Hugo?','Correzione: plurale.',
  ['genitive','plural'],'Hugo'),
 ('it','Genitivo del nome Hugo?','Correzione: dativo.',
  ['dative'],'Hugo'),
 ('it','Genitivo del nome Hugo?','Correzione: dativo, nome Miha.',
  ['dative'],'Miha'),
 ('sl','Kako se glasi rodilnik imena Hugo?','Popravek: ime je »Miha«.',
  ['genitive'],'Miha'),
 ('it','Genitivo del nome Hugo?','Correzione: nome “Miha”.',
  ['genitive'],'Miha'),
 ('sl','Kako se glasi rodilnik imena Hugo?','Popravek: namesto Miha uporabi Luka.',
  ['genitive'],'Luka'),
 ('it','Genitivo del nome Hugo?','Correzione: invece di Miha usa Luka.',
  ['genitive'],'Luka'),
])
def test_explicit_context_correction_updates_signed_slots_and_target(
        language,question,context,facets,target):
 request=plan(question,context,language,{'raw_engines':{}})['request_plan']
 assert request['requested_facets']==facets
 assert [row['text']for row in request['target_spans']]==[target]
 if target=='Miha':
  row=request['target_spans'][0]
  assert row['span_scope']=='context'
  assert (question+'\n'+context)[row['start']:row['end']]==target


@pytest.mark.parametrize('language,question,context,slot',[
 ('sl','Rodilnik ednine imena Hugo?','Popravek: dajalnik.',
  {'case':'dative','number':'singular'}),
 ('it','Genitivo singolare del nome Hugo?','Correzione: dativo.',
  {'case':'dative','number':'singular'}),
 ('sl','Rodilnik ednine imena Hugo?','Popravek: množina.',
  {'case':'genitive','number':'plural'}),
 ('it','Genitivo singolare del nome Hugo?','Correzione: plurale.',
  {'case':'genitive','number':'plural'}),
 ('sl','Rodilnik ednine imena Hugo?',
  'Popravek: dajalnik množine, ime Miha.',
  {'case':'dative','number':'plural'}),
 ('it','Genitivo singolare del nome Hugo?',
  'Correzione: dativo plurale, nome Miha.',
  {'case':'dative','number':'plural'}),
])
def test_current_correction_updates_one_grammar_slot_atomically(
        language,question,context,slot):
 request=plan(question,context,language,{'raw_engines':{}})['request_plan']
 assert request['requested_grammar_slots']==[slot]
 assert 'grammar_slot_pairing_unresolved'not in request['ambiguities']


def test_current_correction_cannot_select_one_of_multiple_grammar_slots():
 question='Genitivo singolare e dativo plurale del nome Hugo?'
 request=plan(question,'Correzione: accusativo.','it',{'raw_engines':{}})['request_plan']
 assert request['requested_grammar_slots']==[]
 assert 'grammar_slot_pairing_unresolved'in request['ambiguities']


def test_historical_grammar_wording_cannot_override_ordered_slot():
 question='Genitivo singolare del nome Hugo?'
 context=('Prima abbiamo discusso questa correzione: invece del dativo plurale '
          'usa il genitivo.')
 request=plan(question,context,'it',{'raw_engines':{}})['request_plan']
 assert request['requested_grammar_slots']==[
  {'case':'genitive','number':'singular'}]

@pytest.mark.parametrize('context,targets',[
 ('Popravek: ime je »Miha«.',['Miha']),
 ('Popravek: ime je »Miha« in ne »Luka«.',['Miha','Luka']),
 ('Popravek: ime je „Miha“.',['Miha']),
 ('Popravek: ime je "Miha".',['Miha']),
 ("Popravek: ime je 'Miha'.",['Miha']),
 ('Popravek: ime je »Miha« ali »Luka«.',['Miha','Luka']),
])
def test_context_correction_reuses_bounded_quote_spans_without_cross_pairing(
        context,targets):
 question='Kako se glasi rodilnik imena Hugo?'
 request=plan(question,context,'sl',{'raw_engines':{}})['request_plan']
 assert [row['text']for row in request['target_spans']]==targets
 assert all(row['span_scope']=='context'for row in request['target_spans'])
 assert all(' ali 'not in target and'in ne'not in target for target in targets)


@pytest.mark.parametrize('language,question,context,target,role_context',[
 ('it','Genitivo del nome Hugo?',
  'Correzione: invece del nome sloveno Hugo usa il nome italiano Miha.',
  'Miha','il nome italiano Miha'),
 ('it','Genitivo del nome Hugo?',
  'Correzione: invece del nome Hugo usa il nome personale Miha.',
  'Miha','il nome personale Miha'),
 ('it','Genitivo del nome Hugo?',
  'Correzione: invece del nome Hugo usa il nome di persona Miha.',
  'Miha','il nome di persona Miha'),
 ('sl','Kako se glasi rodilnik imena Hugo?',
  'Popravek: namesto imena Hugo uporabi ime Miha.',
  'Miha','ime Miha'),
 ('sl','Kako se glasi rodilnik priimka Hugo?',
  'Popravek: namesto priimka »Hugo« uporabi priimek »Miha«.',
  'Miha','priimek »Miha«'),
])
def test_bounded_replacement_frame_signs_only_new_lexical_surface_with_role_context(
        language,question,context,target,role_context):
 request=plan(question,context,language,{'raw_engines':{}})['request_plan']
 assert [row['text']for row in request['target_spans']]==[target]
 row=request['target_spans'][0];combined=question+'\n'+context
 assert combined[row['start']:row['end']]==target
 assert row['span_scope']=='context'
 assert row['context_span']['text']==role_context
 assert combined[row['context_span']['start']:row['context_span']['end']]==role_context
 assert row['context_span']['role']=='replacement'


@pytest.mark.parametrize('context',[
 'Correzione: invece di “Hugo” usa “Miha”.',
 'Correzione: invece di “Hugo” usa Miha.',
 'Correzione: invece di Hugo usa “Miha”.',
 'Correzione: invece del nome “Hugo” usa il nome “Miha”.',
])
def test_replacement_frame_quoted_and_unquoted_combinations_emit_new_only(context):
 question='Genitivo del nome Hugo?'
 request=plan(question,context,'it',{'raw_engines':{}})['request_plan']
 assert [row['text']for row in request['target_spans']]==['Miha']
 row=request['target_spans'][0]
 assert (question+'\n'+context)[row['start']:row['end']]=='Miha'


def test_normalized_noop_replacement_keeps_original_question_authoritative():
 question='Genitivo del nome Hugo?'
 context='Correzione: invece del nome Hugo usa il nome HUGO.'
 request=plan(question,context,'it',{'raw_engines':{}})['request_plan']
 assert [row['text']for row in request['target_spans']]==['Hugo']
 row=request['target_spans'][0]
 assert row.get('span_scope')is None
 assert question[row['start']:row['end']]=='Hugo'


@pytest.mark.parametrize('context',[
 'Correzione: invece del nome Hugo usa il nome tedesco Miha.',
 'Correzione: invece del nome Hugo usa il nome italiano Miha, invece di Luca usa Pino.',
 'Correzione: invece del nome Hugo usa.',
])
def test_unrecognized_or_multiple_replacement_frame_fails_closed(context):
 question='Genitivo del nome Hugo?'
 request=plan(question,context,'it',{'raw_engines':{}})['request_plan']
 assert request['target_spans']==[]


@pytest.mark.parametrize('language,question,context,target,role_context',[
 ('sl','Kako se glasi rodilnik imena Hugo?',
  'Popravek: namesto imena Hugo uporabi ime Miha ali Luka.',
  'Miha ali Luka','ime Miha ali Luka'),
 ('it','Genitivo del nome Hugo?',
  'Correzione: invece del nome Hugo usa il nome Miha e Luca.',
  'Miha e Luca','il nome Miha e Luca'),
])
def test_coordinated_replacement_stays_one_exact_unresolved_surface(
        language,question,context,target,role_context):
 request=plan(question,context,language,{'raw_engines':{}})['request_plan']
 assert request['ambiguities']==['coordinated_target_unresolved']
 assert [row['text']for row in request['target_spans']]==[target]
 row=request['target_spans'][0];combined=question+'\n'+context
 assert row['basis']=='ambiguous correction replacement; original surface'
 assert combined[row['start']:row['end']]==target
 assert row['context_span']['text']==role_context
 assert combined[row['context_span']['start']:row['context_span']['end']]==role_context


def test_historical_replacement_description_does_not_override_current_question():
 question='Genitivo del nome Hugo?'
 context=('Prima abbiamo discusso il dativo e questa correzione: invece del nome Hugo '
          'usa il nome italiano Miha.')
 request=plan(question,context,'it',{'raw_engines':{}})['request_plan']
 assert [row['text']for row in request['target_spans']]==['Hugo']
 assert request['target_spans'][0].get('span_scope')is None
 assert request['requested_facets']==['genitive']

def test_ordinary_history_never_overrides_current_slots_but_can_supply_referential_target():
 current=plan('Come si declina il nome Hugo?',
  'Prima abbiamo discusso il genitivo plurale.','it',{'raw_engines':{}})['request_plan']
 assert current['requested_facets']==['inflection']
 assert [row['text']for row in current['target_spans']]==['Hugo']
 referential=plan('Kako se to sklanja?',
  'Prej smo vprašali: rodilnik imena Hugo?','sl',{'raw_engines':{}})['request_plan']
 assert referential['requested_facets']==['inflection']
 assert [row['text']for row in referential['target_spans']]==['Hugo']
 assert referential['target_spans'][0]['span_scope']=='context'

@pytest.mark.parametrize('text',[
 'Kako se v slovenščino prevede didattica a distanza?',
 'Kako se didattica a distanza prevede v slovenščino?',
 'Kako prevedemo didattica a distanza v slovenščino?',
 'Kako se v slovenščini reče didattica a distanza?',
 'Prevedi izraz didattica a distanza v slovenščino.',
 'Kakšen je prevod izraza didattica a distanza?',
 'Kaj je slovenska ustreznica za didattica a distanza?',
 'Come si traduce didattica a distanza in sloveno?',
 'Qual è la traduzione di didattica a distanza?',
])
def test_unquoted_translation_preserves_whole_original_object(text):
 p=plan(text,'','und',{'raw_engines':{}})
 assert [t['text']for t in p['targets']]==['didattica a distanza']
 target=p['targets'][0]
 assert text[target['start']:target['end']]==target['text']
 assert 'translation'in p['requested_facets']
 assert target['basis']=='unquoted translation object; whole original span'

@pytest.mark.parametrize('term',['attività didattica','tempo pieno','pouk na daljavo'])
def test_unquoted_translation_span_is_not_tied_to_one_term(term):
 text=f'Kako prevedemo {term} v italijanščino?'
 p=plan(text,'','sl',{'raw_engines':{}})
 assert [t['text']for t in p['targets']]==[term]

def test_translation_keeps_function_words_when_analyzers_nominate_only_components():
 text='Kako se v slovenščino prevede didattica a distanza?'
 hints=[]
 for word in ['didattica','distanza']:
  start=text.index(word)
  hints.append({'text':word,'start':start,'end':start+len(word),'upos':'NOUN','lemma':word,'feats':{}})
 p=plan(text,'','sl',{'raw_engines':{'fixture':{'analysis':{'words':hints}}}})
 assert [t['text']for t in p['targets']]==['didattica a distanza']
 assert p['targets'][0]['lemma_candidates']==['didattica','distanza']

@pytest.mark.parametrize('text,target',[
 ('didattica a distanza','didattica a distanza'),
 ('Kako se v slovenščini reče »didattica a distanza«?','didattica a distanza'),
 ('lepem dnevu','lepem dnevu'),
 ('Kaj pomeni izraz lepa beseda?','lepa beseda'),
])
def test_translation_frames_preserve_quoted_bare_and_other_phrase_queries(text,target):
 p=plan(text,'','und',{'raw_engines':{}})
 assert [t['text']for t in p['targets']]==[target]

@pytest.mark.parametrize('text,targets,contexts',[
 ('Je prav "Luka ni" ali "Luke ni"?',['Luka','Luke'],['Luka ni','Luke ni']),
 ('Je prav »brez Alfe« ali »brez Alfa«?',['Alfe','Alfa'],['brez Alfe','brez Alfa']),
 ('Je prav "Marka ni" ali "Marko ni"?',['Marka','Marko'],['Marka ni','Marko ni']),
])
def test_quoted_grammatical_contrast_keeps_clause_context_and_lexical_forms(text,targets,contexts):
 p=plan(text,'','sl',{'raw_engines':{}})
 assert [t['text']for t in p['targets']]==targets
 assert [t['context_span']['text']for t in p['targets']]==contexts
 assert p['slot_focus']['case']=='genitive'
 assert p['query_kind']=='comparison'
 for target in p['targets']:
  assert text[target['start']:target['end']]==target['text']
  clause=target['context_span']
  assert text[clause['start']:clause['end']]==clause['text']

@pytest.mark.parametrize('text,targets',[
 ('Je prav "Novo mesto" ali "Novo Mesto"?',['Novo mesto','Novo Mesto']),
 ('Je prav "lep dan" ali "lepa noč"?',['lep dan','lepa noč']),
 ('Je prav "brez dela" ali "brez"?',['brez dela','brez']),
 ('Ali je "Luka ni" pravilno? Zakaj je "Luke ni" redko?',['Luka ni','Luke ni']),
 ('Kako se prevede "Luka ni" ali "Luke ni"?',['Luka ni','Luke ni']),
])
def test_quoted_comparison_does_not_strip_content_or_empty_an_alternative(text,targets):
 assert [t['text']for t in plan(text,'','sl',{'raw_engines':{}})['targets']]==targets

@pytest.mark.parametrize('text',[
 'Je prav "Luka ni zdrav" ali "Luke ni zdrav"?',
 'Luka ni zdrav.',
 'Je prav "lep dan" ali "lepa noč", če ni dežja?',
])
def test_negation_with_a_predicate_is_not_a_genitive_existence_frame(text):
 assert plan(text,'','sl',{'raw_engines':{}})['slot_focus']is None

def test_nonidentical_tokenization_aligns_without_zip():
 def raw(tokens):
  return {'analysis':{'tokens':[{'id':i,'start':a,'end':b,'text':s}for i,(a,b,s)in enumerate(tokens)],'words':[{'parent_token_id':i,'text':s,'lemma':s,'upos':'X','xpos':'X','feats':{}}for i,(_,_,s)in enumerate(tokens)]}}
 result=align({'a':raw([(0,3,'a-b')]),'b':raw([(0,1,'a'),(1,2,'-'),(2,3,'b')])},'a-b')
 assert len(result)==1 and result[0]['members']=={'b':[0,1,2],'a':[0]} and result[0]['disagreement']
 with pytest.raises(ValueError):align({'a':raw([(0,3,'wrong')])},'a-b')
