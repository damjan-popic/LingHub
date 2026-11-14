"""Pure CORE-plan→lexical-profile projection, with no resource/service calls."""
from copy import deepcopy
import pytest
from core.lexical_planner import plan
from core.lexical_profile import synonym_source_fields
from tools.zrc_reference.request_interpretation import authoritative_request_plan


@pytest.mark.parametrize('q,language,targets',[
 ('miza','sl',['miza']),('sprejeti odločitev','sl',['sprejeti odločitev']),
 ('v zvezi s tem','sl',['v zvezi s tem']),('molto bene','it',['molto bene']),
 ('Santa Maria','it',['Santa Maria']),('Danes dežuje.','sl',[]),
 ('Domani piove.','it',[]),('Je beseda miza primerna v dopisu?','sl',['miza']),
 ('La parola scuola è appropriata?','it',['scuola']),
 ('Rodilnik od besede miza v dvojini.','sl',['miza']),
])
def test_supplied_shared_scope_survives_profile_planning_without_a_second_target_vote(q,language,targets):
 shared=authoritative_request_plan(q,input_language=language,locale=language)
 # Synthetic analyzer nominations cannot fill or replace authoritative targets.
 analysis={'raw_engines':{'fixture':{'analysis':{'words':[{
     'text':'drugo','lemma':'drugo','upos':'NOUN','feats':{},'start':0,'end':5}]}}}}
 projected=plan(q,'',language,analysis,request_plan=shared)
 assert [t['text']for t in projected['targets']]==targets
 assert projected['request_plan']==shared
 assert projected['requested_facets']==shared['requested_facets']
 if shared.get('consultation'):
  assert shared['consultation']['requested']is False
 else:assert shared['requested_grammar_slots']


def test_native_entry_and_sense_qualifications_remain_separate_and_unchanged():
 entry={'headword':'miza','head_status':'fixture',
     'head_metadata':['Only this field',{'label':'regional'}]}
 sense={'sense_id':'fixture:1','metadata':['Only this sense'],'relations':[]}
 originals=deepcopy((entry,sense));fields=synonym_source_fields(entry,sense)
 assert (entry,sense)==originals
 assert fields['head_metadata']==entry['head_metadata']
 assert fields['sense']['metadata']==sense['metadata']
 fields['head_metadata'].append('changed view')
 assert (entry,sense)==originals
