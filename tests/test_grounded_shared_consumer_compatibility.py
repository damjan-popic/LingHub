"""Pure producer/consumer check in their existing separate Python environments."""
from copy import deepcopy
from pathlib import Path
import json,os,subprocess
import pytest
from test_pilot_unified import run_bounded_answer_plan,frozen_matches_from_result,core_test_root,unified

PRODUCER=r"""
import sys,json,copy
from pathlib import Path
root=Path(sys.argv[1]);case=sys.argv[2]
sys.path[:0]=[str(root/'apps/api'),str(root),str(root/'tests/zrc')]
from test_grounded_selected_consumers import prepared
if case=='COLL-P':
 from test_scoped_lexical_relations import prepared
_,plan,payload,rt,registry=prepared(case)
if case=='EPR-P':
 from tools.zrc_reference.pilot_contracts import GenerateRequest
 full=json.loads(Path(sys.argv[3]).read_text())['bundle']['gateway_bundle']['payload']
 full['query_plan']=plan
 rt._unpack=lambda _:copy.deepcopy(full)
 rt._check_source=lambda _:None
 rt.answer_writer_profile='isolated-existing-profile-contract'
 def forbidden(*args,**kwargs):raise AssertionError('No endpoint/native/model action')
 rt._writer_request=rt._tokens=rt._identity=forbidden
 result=rt.generate(GenerateRequest(bundle={},tier='answer',model='auto'))
else:
 selection={'answer_language':'sl','blocks':[{'block_id':'B'+str(i+1),'unit_id':u['unit_id'],'paragraph_id':'P1'}for i,u in enumerate(registry.units)],'no_sufficient_evidence':False}
 result=rt._process_fresh_answer_plan(selection,registry,payload=payload,tier='answer')
print(json.dumps(result,ensure_ascii=False))
"""


def produced(case):
 root=core_test_root()
 python=os.environ.get('KLIKAI_CORE_TEST_PYTHON','/data/envs/lingstack/klikai/bin/python')
 source=os.environ.get('KLIKAI_SAVED_EPR_FREEZE','')
 if case=='EPR-P'and not source:pytest.skip('Retained private EPR freeze path not configured')
 result=subprocess.run([python,'-c',PRODUCER,str(root),case,source],check=True,capture_output=True,text=True)
 return json.loads(result.stdout)


def test_real_core_native_publisher_zero_dispatch_result_crosses_auto_web_consumer():
 result=produced('EPR-P')
 assert result['execution']['inference_performed']is False and result['model_identity']['model_id']=='not_invoked'
 assert result['provider_attempts']==[]and all(v==0 for v in result['generation_accounting'].values())
 matches=[]
 for evidence in result['evidence']:
  identity=unified.evidence_id(evidence)
  match=deepcopy(next(item for item in result['matches']if unified.evidence_id(item)==identity))
  match['_citation_binding']=unified.frozen_citation_binding(match,dict(evidence,citation_id=identity))
  assert match['_citation_binding']
  matches.append(match)
 output=run_bounded_answer_plan(result,matches,model='auto')
 assert output['generation']['status']=='generated',output
 assert output['answer_md']==result['answer_md']and output['citations']
 assert 'tožilnik ednine: »Jupiter«'in output['answer_md']and'nimajo normativne veljave'in output['answer_md']
 assert {c['source_key']for c in output['citations']}=={'epravopis-slovar'}


def test_real_core_related_component_result_preserves_source_owners_and_citations_in_web():
 result=produced('TERM-P')
 output=run_bounded_answer_plan(result,frozen_matches_from_result(result))
 assert output['generation']['status']=='generated',output
 assert output['answer_md']==result['answer_md']and len(output['citations'])==2
 for value in ['pogodba za krajši delovni čas','contratto a tempo parziale','AI','Italia','ne potrjuje enakovrednosti']:
  assert value in output['answer_md']
 assert {c['source_key']for c in output['citations']}=={'crossterm','zvezek'}


def test_real_core_concise_metric_recomposition_keeps_each_record_citation_in_web():
 result=produced('COLL-P')
 output=run_bounded_answer_plan(result,frozen_matches_from_result(result))
 assert output['generation']['status']=='generated',output
 assert output['answer_md']==result['answer_md']and len(output['citations'])==3
 assert output['answer_md'].count('Pogostnost je število')==1
 assert output['answer_md'].count('Za »pogodbo«')==1
 assert {c['source_key']for c in output['citations']}=={'cjvt-ksss'}


@pytest.mark.parametrize('damage',['source_key','source_revision','snippet'])
def test_new_component_mode_does_not_relax_existing_frozen_citation_validation(damage):
 result=produced('TERM-P');matches=frozen_matches_from_result(result)
 result=deepcopy(result);result['citations'][0][damage]='altered'
 output=run_bounded_answer_plan(result,matches)
 assert output['generation']['status']!='generated'
