import json
import pytest
from pilot_backend.classification import method,contains_generated_text
from pilot_backend.jobs import render_result

@pytest.mark.parametrize('value,expected,generated',[
 ({'answer_kind':'generated'},'generated',True),
 ({'answer_kind':'generated','synthesis_mode':'source_supported_partial'},'source_supported_partial',False),
 ({'answer_kind':'source_only','generation_status':'source_supported_partial','synthesis_mode':'source_supported_partial'},'source_supported_partial',False),
 ({'answer_kind':'mixed','synthesis_mode':'mixed_generated_and_source_partial'},'mixed_generated_and_source_partial',True),
 ({'answer_kind':'source_only'},'source_only',False),
 ({'answer_kind':'clarification_required'},'clarification_required',False),
 ({'status':'no_hit','answer_kind':'source_only'},'no_hit',False),
 ({'status':'failed','answer_kind':'generated'},'failed',False),
])
def test_methods_distinguish_generated_text_from_source_repairs(value,expected,generated):
 before=json.dumps(value,sort_keys=True)
 assert method(value)==expected and contains_generated_text(value)is generated
 assert json.dumps(value,sort_keys=True)==before

def test_safe_result_preserves_method_but_not_private_raw_response():
 value={'status':'validated','answer_md':'Original quotation.','short_answer':'Original quotation.','synthesis_mode':'source_supported_partial','validation':{'status':'bounded_checks_completed'},'quality_actions':[{'action':'source_supported_partial'}],'quality_timings':{'repair_ms':1},'raw_response':{'reasoning':'PRIVATE'}}
 out,_=render_result(value)
 assert out['synthesis_mode']=='source_supported_partial'and out['quality_actions']==value['quality_actions']
 assert 'raw_response'not in out


def test_replay_all_source_repairs_do_not_count_as_generated(tmp_path):
 from pilot_backend import replay
 from pilot_backend.store import Store,canonical
 from test_pilot_backend_replay import FakeGateway
 class SourceGateway(FakeGateway):
  def request(self,path,payload):
   out=super().request(path,payload)
   if path.endswith('/generate'):out.update(answer_kind='generated',synthesis_mode='source_supported_partial')
   return out
 data={'schema':replay.SCHEMA,'origin':'previously_exposed_regression','cases':[{'case_id':'synthetic-case','question':'Synthetic.','context':'','locale':'sl','task':'reference','tier':'short','model':'qwen','sources':['jezikovna_svetovalnica'],'source_groups':['synthetic-source'],'original_case_sha256':'a'*64,'exposure_set':'exposed_regression'}]}
 path=tmp_path/'cases.json';path.write_text(canonical(data));path.chmod(0o600)
 store=Store(tmp_path/'private'/'pilot.sqlite');result=replay.run(store,SourceGateway(),path,replay.file_sha(path),force_test=True)
 assert result['status']=='COMPLETE'and result['model_generated_outputs']==0
 assert result['output_method_counts']=={'source_supported_partial':2}


def test_new_analysis_preserves_prior_cycle_bytes(tmp_path,monkeypatch):
 from pilot_backend import cycles
 from pilot_backend.store import Store
 store=Store(tmp_path/'private'/'pilot.sqlite')
 version=cycles.VERSION;monkeypatch.setattr(cycles,'VERSION','historical-analysis-fixture')
 old=cycles.analyse(store)
 with store.connect()as db:old_bytes=db.execute('SELECT report FROM cycles WHERE id=?',(old['cycle_id'],)).fetchone()[0]
 monkeypatch.setattr(cycles,'VERSION',version);new=cycles.analyse(store)
 assert new['cycle_id']!=old['cycle_id']and new['counts']['output_classification_version']=='pilot-output-method-v2'
 with store.connect()as db:assert db.execute('SELECT report FROM cycles WHERE id=?',(old['cycle_id'],)).fetchone()[0]==old_bytes
