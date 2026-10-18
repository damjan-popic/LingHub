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
