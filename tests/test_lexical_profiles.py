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

def test_nonidentical_tokenization_aligns_without_zip():
 def raw(tokens):
  return {'analysis':{'tokens':[{'id':i,'start':a,'end':b,'text':s}for i,(a,b,s)in enumerate(tokens)],'words':[{'parent_token_id':i,'text':s,'lemma':s,'upos':'X','xpos':'X','feats':{}}for i,(_,_,s)in enumerate(tokens)]}}
 result=align({'a':raw([(0,3,'a-b')]),'b':raw([(0,1,'a'),(1,2,'-'),(2,3,'b')])},'a-b')
 assert len(result)==1 and result[0]['members']=={'b':[0,1,2],'a':[0]} and result[0]['disagreement']
 with pytest.raises(ValueError):align({'a':raw([(0,3,'wrong')])},'a-b')
