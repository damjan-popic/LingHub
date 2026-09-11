import pytest
from pilot_backend.collation import plan,collect
from pilot_backend.store import digest
from pilot_backend.security import Failure

def test_explicit_operation_and_single_focus_only():
 assert plan('Kako sklanjam Hugo?','sl')is None
 assert plan('Pojasni morfologijo »dveh besed«.','und')['reason']=='input_language_required'
 assert plan('Sopomenke za »lep« in »dober«.','sl')['reason']=='one_quoted_focus_required'
 assert plan('Per «delibere» controlla morfologia e termini.','it')['operations']==['terminology','morphology']
 assert plan('What does LORIS do?','sl')['reason']=='one_quoted_focus_required'

def test_no_language_from_italian_answer_locale():
 class G:
  def request(self,*args):raise AssertionError('No calls on clarification')
 result=collect(G(),{'question':'Poišči sopomenke za »beseda«.','locale':'it','input_language':'und'})
 assert result['plan']['reason']=='input_language_required'and result['captures']==[]

def test_capability_drift_fails_closed():
 class G:
  linghub_url='http://127.0.0.1:8010';klikai_url=None;n=0
  def request(self,path,payload,base):
   if path.endswith('capabilities'):
    self.n+=1;return {'identity':str(self.n),'analyzer_service_reachable':True}
   return {'text':'lep','lang':'sl','offset_convention':'unicode_codepoint_half_open'}
 with pytest.raises(Failure,match='resource_changed'):collect(G(),{'question':'Sopomenke za »lep«.','locale':'it','input_language':'sl'})

def test_requested_tools_only_and_immutable_capture():
 class G:
  linghub_url='http://127.0.0.1:8010';klikai_url=None
  def request(self,path,payload,base):
   if path.endswith('capabilities'):return {'identity':'a'*64,'analyzer_service_reachable':True}
   assert payload['include']==['synonyms'];assert payload['lang']=='sl'
   return {'text':'lep','lang':'sl','offset_convention':'unicode_codepoint_half_open','results':{'synonyms':{'status':'no_hit','data':{}}}}
 r=collect(G(),{'question':'Sopomenke za »lep«.','locale':'it','input_language':'sl'})
 assert r['captures'][0]['response_sha256']==digest(r['captures'][0]['response'])
 assert r['capture_set_sha256']==digest(r['captures'])

def test_adapter_passes_explicit_tool_capture_to_private_freeze_only():
 from pilot_backend.adapter import Gateway
 class G(Gateway):
  def __init__(self):super().__init__('http://127.0.0.1:18320','synthetic-not-a-secret-xxxxxxxxxxxxxxxxx',linghub_url='http://127.0.0.1:8010');self.calls=[]
  def request(self,path,payload,base=None):
   self.calls.append((path,payload,base))
   if path.endswith('capabilities'):return {'identity':'a'*64,'analyzer_service_reachable':True}
   if path=='/v1/explore':return {'text':'lep','lang':'sl','offset_convention':'unicode_codepoint_half_open','results':{'synonyms':{'status':'no_hit','data':{}}}}
   assert path=='/internal/pilot/freeze'and base is None
   return {'bundle':payload,'identity':{'synthetic':True}}
 g=G();f={'task':'reference','question':'Sopomenke za »lep«.','locale':'it','input_language':'sl','context':''}
 result=g.freeze(f,{})
 assert result['bundle']['tool_evidence']['captures'][0]['request']['lang']=='sl'
 assert all(path in {'/v1/capabilities','/v1/explore','/internal/pilot/freeze'}for path,_,_ in g.calls)
 assert all(base=='http://127.0.0.1:8010'for path,_,base in g.calls if not path.startswith('/internal'))


def test_job_instrumentation_keeps_blind_public_response_free_of_timings(tmp_path):
 import json
 from argon2 import PasswordHasher,Type
 from pilot_backend.api import create_app
 class Adapter:
  def freeze(self,family,policy):return {'bundle':{'frozen':True},'identity':{'synthetic':True}}
  def generate(self,*args):return {'status':'validated','short_answer':'Synthetic.','answer_md':'Synthetic.','evidence':[],'source_badges':[],'timings':{'generation_ms':1}}
 app=create_app({'store':str(tmp_path/'own'/'pilot.sqlite'),'allowed_origins':['http://127.0.0.1:18310'],'secure_cookies':False},adapter=Adapter(),hasher=PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1,type=Type.ID),start_workers=False)
 owner=app.state.auth.create('synthetic','long private test password',role='tester',origin='synthetic',must_change=False)
 d=app.state.domain
 f=d.create_family(owner,{'question':'Synthetic.','locale':'sl','task':'reference','idempotency_key':'synthetic-timing-key'})
 job=app.state.jobs.claim();app.state.jobs.execute(job)
 with app.state.store.connect()as db:
  result=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(job['run_id'],)).fetchone()[0])
 assert result['timings']['queue_wait_ms']>=0 and result['timings']['family_freeze_elapsed_ms']>=0
 assert result['timings']['first_useful_display_ms']is None
 assert 'timings'not in str(d.family(owner,f['family']['answer_family_id']))


def test_italian_endings_and_descriptive_terminology_do_not_dispatch_tools():
 for question in ['Come termina il nome «Luca»?','Come terminare la frase «Saluti»?','Che terminazione ha «Luca»?','Pojasni terminološka načela.','Katera so terminološka načela?','Quali sono i principi della terminologia?','Kaj pomeni beseda »sopomenka«?']:
  assert plan(question,'sl')is None
 for question,language in [('Poišči terminološko ustreznico za »izraz«.','sl'),('Preveri termin »izraz«.','sl'),('Trova gli equivalenti per «delibere».','it'),('Verifica la terminologia per «delibere».','it'),('Equivalenti per «delibere».','it'),('Kateri termin uporabimo za »izraz«?','sl'),('Katere so ustreznice za »izraz«?','sl'),('Quali sono gli equivalenti di «delibere»?','it')]:
  assert plan(question,language)['operations']==['terminology']
 assert plan('Poišči terminološko ustreznico.','sl')['reason']=='one_quoted_focus_required'


def test_real_job_path_preserves_private_freeze_stages_and_marks_expansion_reuse(tmp_path):
 import json
 from argon2 import PasswordHasher,Type
 from pilot_backend.api import create_app
 class Adapter:
  count=0
  def freeze(self,family,policy):
   self.count+=1
   return {'bundle':{'frozen':True},'identity':{'source_model_profile_pin':'a'*64},'timings':{'cpu_collation_ms':9,'tool_evidence_ms':2,'lexical_ms':3,'embedding_ms':4,'rerank_ms':5,'tokenizer_ms':6,'source_text':'PRIVATE','vector_search_ms':float('nan')}}
  def generate(self,*args):return {'status':'validated','short_answer':'Synthetic.','answer_md':'Synthetic.','evidence':[],'source_badges':[],'timings':{'generation_ms':11,'tokenizer_ms':7,'lexical_ms':None}}
 adapter=Adapter();app=create_app({'store':str(tmp_path/'own'/'pilot.sqlite'),'allowed_origins':['http://127.0.0.1:18310'],'secure_cookies':False},adapter=adapter,hasher=PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1,type=Type.ID),start_workers=False)
 owner=app.state.auth.create('synthetic','long private test password',role='tester',origin='synthetic',must_change=False);d=app.state.domain
 created=d.create_family(owner,{'question':'Synthetic.','locale':'sl','task':'reference','idempotency_key':'synthetic-freeze-timing-key'});fid=created['family']['answer_family_id']
 first=app.state.jobs.claim();app.state.jobs.execute(first)
 with app.state.store.connect()as db:
  identity_raw=db.execute('SELECT identity FROM families WHERE id=?',(fid,)).fetchone()[0];identity=json.loads(identity_raw);short=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(first['run_id'],)).fetchone()[0])
 assert identity['source_model_profile_pin']=='a'*64 and 'PRIVATE'not in identity_raw
 assert identity['freeze_observation']['stages']=={'cpu_collation_ms':9,'tool_evidence_ms':2,'lexical_ms':3,'embedding_ms':4,'rerank_ms':5,'freeze_tokenizer_ms':6}
 assert short['timings']['lexical_ms']==3 and short['timings']['cpu_collation_ms']==9
 assert short['timings']['tokenizer_ms']==7 and short['timings']['freeze_tokenizer_ms']==6
 assert 'timings'not in str(d.family(owner,fid))and'freeze_observation'not in str(d.family(owner,fid))
 d.expand(owner,fid,{'idempotency_key':'synthetic-expand-timing-key'});second=app.state.jobs.claim();app.state.jobs.execute(second)
 with app.state.store.connect()as db:
  long=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(second['run_id'],)).fetchone()[0]);assert db.execute('SELECT identity FROM families WHERE id=?',(fid,)).fetchone()[0]==identity_raw
 assert adapter.count==1 and long['timings']['evidence_cache_reused']is True
 assert long['timings']['family_freeze_elapsed_ms']==0 and long['timings']['lexical_ms']is None
 assert long['timings']['freeze_observation_scope']=='retained_prior_family_freeze'
 assert 'cpu_collation_ms'not in long['timings']
