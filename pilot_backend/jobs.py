"""Persisted bounded work with short-job priority and starvation prevention."""
from __future__ import annotations
import fcntl,json,math,re,threading,time
from .store import canonical,digest,stamp
from .security import Failure
from .classification import method
from .human_feedback import record_stage, safe_exception, terminal_display


FREEZE_STAGES={'cpu_collation_ms','tool_evidence_ms','lexical_ms','embedding_ms','vector_search_ms','rerank_ms','retrieval_and_freeze_ms','evidence_lookup_ms','registry_ms','source_collection_ms','gateway_freeze_ms','evidence_preparation_ms'}

def freeze_observation(frozen,elapsed_ms):
    """Private numeric observations, outside the immutable source/model pin."""
    raw=frozen.get('timings')or {};stages={}
    for name in FREEZE_STAGES|{'tokenizer_ms'}:
        value=raw.get(name)
        if type(value)in {int,float}and math.isfinite(value)and value>=0:
            stages['freeze_tokenizer_ms'if name=='tokenizer_ms'else name]=value
    return {'version':'pilot-freeze-observation-v1','adapter_elapsed_ms':elapsed_ms,'stages':stages}


def render_result(value):
    if not isinstance(value,dict):raise Failure('invalid_answer',502)
    status=value.get('status')
    if status not in {'validated','ok','no_hit','no_answer','source_only','partial','rejected','failed'}:raise Failure('invalid_answer',502)
    allowed={'status','short_answer','answer_md','source_badges','evidence','display_blocks','blocks','display_sha256','claims','citations','answer_pin','evidence_packet_hash','source_snapshot_ids','vector_snapshot_id','policy_version','locale','tier','generation_profile_id','model_identity','execution','usage','timings','family_basis_hash','limitations','correction','answer_support','answer_kind','generation_status','word_count','length_status','omitted_claims','deterministic_result','critical_safety_or_provenance_error','synthesis_mode','validation','quality_actions','quality_timings','clarification_code','retrieval','generation','progress','decision_sources'}
    out={k:v for k,v in value.items()if k in allowed}
    from .human_feedback import provider_diagnostics
    out.update(provider_diagnostics(value))
    for field in ('short_answer','answer_md'):
        if not isinstance(out.get(field,''),str)or len(out.get(field,''))>100000:raise Failure('invalid_answer',502)
        out.setdefault(field,'')
    for field in ('source_badges','evidence'):
        if not isinstance(out.get(field,[]),list):raise Failure('invalid_answer',502)
        out.setdefault(field,[])
    if 'decision_sources' in out:
        rows=out['decision_sources']
        if (not isinstance(rows,list) or len(rows)>64 or any(not isinstance(row,dict)
                or set(row)!={'evidence_id','title'} or not isinstance(row.get('evidence_id'),str)
                or not row['evidence_id'] or len(row['evidence_id'])>2000
                or not isinstance(row.get('title'),str) or not row['title'].strip()
                or len(row['title'])>2000 for row in rows)):
            raise Failure('invalid_answer',502)
    # The UI renders these canonical blocks as text. Anchors address Unicode code points.
    blocks=out.get('display_blocks')or out.get('blocks')
    if blocks:
        if not isinstance(blocks,list)or len(blocks)>100 or any(not isinstance(b,dict)or not isinstance(b.get('text'),str)for b in blocks):raise Failure('invalid_answer',502)
        for b in blocks:
            if 'evidence_ids'in b and(not isinstance(b['evidence_ids'],list)or len(b['evidence_ids'])>256 or any(not isinstance(eid,str)for eid in b['evidence_ids'])):raise Failure('invalid_answer',502)
            if 'paragraph_id'in b and(not isinstance(b['paragraph_id'],str)or not re.fullmatch(r'P(?:[1-9]|1[0-4])',b['paragraph_id'])):raise Failure('invalid_answer',502)
        blocks=[{'block_id':str(b.get('block_id',f'b{i}')),'text':b['text'],**({'evidence_ids':list(b['evidence_ids'])}if'evidence_ids'in b else{}),**({'paragraph_id':b['paragraph_id']}if'paragraph_id'in b else{})}for i,b in enumerate(blocks)]
        if len({b['block_id']for b in blocks})!=len(blocks):raise Failure('invalid_answer',502)
    else:blocks=[{'block_id':'answer','text':out['answer_md']or out['short_answer']}]
    out['blocks']=blocks
    return out,digest(blocks)

class Jobs:
    def __init__(self,store,adapter,workers=2,release=None):
        self.store=store;self.adapter=adapter;self.workers=workers;self.stop_event=threading.Event();self.threads=[];self.lock=None;self.release=release
    def start(self):
        if self.threads:return
        self.lock=self.store.path.with_suffix('.workers.lock').open('a')
        try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:self.lock.close();self.lock=None;raise RuntimeError('A pilot worker instance is already active')from None
        self.store.recover_jobs()
        self.threads=[threading.Thread(target=self.loop,args=('model',),name=f'pilot-worker-{i}',daemon=True)for i in range(self.workers)]
        self.threads.append(threading.Thread(target=self.loop,args=('cpu',),name='pilot-worker-cpu',daemon=True))
        for t in self.threads:t.start()
    def close(self,grace_seconds=100):
        self.stop_event.set()
        deadline=time.monotonic()+max(0,grace_seconds)
        for t in self.threads:t.join(timeout=max(0,deadline-time.monotonic()))
        # Retain the lock while an in-flight request is completing; a replacement must fail closed.
        if not any(t.is_alive()for t in self.threads)and self.lock:self.lock.close();self.lock=None
    def claim(self,pool=None):
        with self.store.tx()as db:
            rows=list(db.execute("SELECT j.*,r.family_id FROM jobs j JOIN runs r ON r.id=j.run_id WHERE j.state='queued'ORDER BY CASE WHEN j.created<?THEN 0 WHEN j.kind='short'THEN 1 ELSE 2 END,j.created",(stamp()-60,)))
            rows=[r for r in rows if pool is None or(pool=='cpu')==(r['model']=='cpu')]
            busy=list(db.execute("SELECT j.owner,j.model,r.family_id FROM jobs j JOIN runs r ON r.id=j.run_id WHERE j.state='running'"))
            # One job/account and one/model prevents a comparison or long expansion monopolizing GPUs.
            row=next((r for r in rows if all((r['owner']!=b['owner']or'cpu'in {r['model'],b['model']})and r['model']!=b['model']and r['family_id']!=b['family_id']for b in busy)),None)
            if not row:return None
            claimed_at=stamp()
            db.execute("UPDATE jobs SET state='running',started=?,attempts=attempts+1 WHERE id=?AND state='queued'",(claimed_at,row['id']))
            db.execute("UPDATE runs SET state='running'WHERE id=?",(row['run_id'],));return dict(row,started=claimed_at)
    def continue_stage(self,job,result=None):
        """Observe cancellation before starting another bounded operation.

        The synchronous provider has no per-request abort contract. Its current
        call may finish, but cancellation must not start the next stage or
        terminate the shared model process.
        """
        with self.store.tx()as db:
            current=db.execute('SELECT state,cancel_requested FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if not current or current['state']!='running':return False
            if current['cancel_requested']:
                finished=stamp()
                db.execute("UPDATE jobs SET state='cancelled',finished=? WHERE id=?",(finished,job['id']))
                display,display_hash=terminal_display(db,job['run_id'],'cancelled')
                db.execute("UPDATE runs SET state='cancelled',result=?,display_hash=?,finished=? WHERE id=?",(display,display_hash,finished,job['run_id']))
                return False
            if result is not None:db.execute('UPDATE runs SET result=? WHERE id=?',(canonical(result),job['run_id']))
            return True
    def execute(self,job):
        started=time.monotonic();rid=job['run_id'];queue_wait_ms=max(0,((job.get('started')or stamp())-job['created'])*1000);freeze_ms=0.0;matches_ready_ms=None
        stage='accepted'
        try:
            record_stage(self.store,rid,'started',{'queue_wait_ms':queue_wait_ms,'timestamp':stamp()})
            if not self.continue_stage(job):return
            with self.store.connect()as db:
                r=dict(db.execute('SELECT *FROM runs WHERE id=?',(rid,)).fetchone());f=dict(db.execute('SELECT *FROM families WHERE id=?',(r['family_id'],)).fetchone());policy=json.loads(db.execute('SELECT config FROM policies WHERE id=?',(f['policy_id'],)).fetchone()[0])
                parent=db.execute('SELECT result FROM runs WHERE id=? AND family_id=?',(r['parent_run_id'],r['family_id'])).fetchone()if r['parent_run_id']else None
                focus=db.execute('SELECT focus FROM family_focus WHERE family_id=?',(f['id'],)).fetchone()
                if focus:f['focus']=focus[0]
                original=db.execute('SELECT original_question FROM family_inputs WHERE family_id=?',(f['id'],)).fetchone()
                if original:f['original_question']=original[0]
            is_unified=f['task']=='reference'and getattr(self.adapter,'unified_answers',False)
            if is_unified:
                if not self.continue_stage(job,{'progress':{'stage':'retrieval'if not f['bundle']else'generation'}}):return
            if f['task']=='reference'and not is_unified and hasattr(self.adapter,'require_generation'):self.adapter.require_generation([r['model']])
            if not f['bundle']:
                freeze_started=time.monotonic()
                stage='retrieval_and_freeze'
                frozen=self.adapter.freeze(f,policy)
                freeze_ms=(time.monotonic()-freeze_started)*1000
                if not isinstance(frozen.get('bundle'),dict)or not isinstance(frozen.get('identity'),dict):raise Failure('invalid_freeze',502)
                identity=dict(frozen['identity'],freeze_observation=freeze_observation(frozen,freeze_ms))
                with self.store.tx()as db:
                    db.execute('UPDATE families SET bundle=?,identity=?WHERE id=?AND bundle IS NULL',(canonical(frozen['bundle']),canonical(identity),f['id']))
                    current=db.execute('SELECT bundle,identity FROM families WHERE id=?',(f['id'],)).fetchone()
                    if current is None:return
                    bundle=json.loads(current[0]);observation=json.loads(current[1]).get('freeze_observation')
            else:
                bundle=json.loads(f['bundle']);observation=json.loads(f['identity']or'{}').get('freeze_observation')
            record_stage(self.store,rid,'freeze',{'family_id':f['id'],'frozen_reference':'families.bundle','reused':bool(f['bundle'])})
            if is_unified:
                matches_ready_ms=queue_wait_ms+(time.monotonic()-started)*1000
                if not self.continue_stage(job,{'progress':{'stage':'generation','matches_ready':True},'retrieval':bundle.get('retrieval',{})}):return
            elif not self.continue_stage(job):return
            stage='writer'
            generated=self.adapter.generate(bundle,r['tier'],r['model'],json.loads(parent[0])if parent and parent[0]else None)
            record_stage(self.store,rid,'generation',generated.get('diagnostics',{'writer':{'status':'not_invoked','reason':generated.get('generation',{}).get('status')}}) if generated.get('generation',{}).get('status') in {'no_support','unavailable'} else generated.get('diagnostics',{'writer':{'status':'not_captured','missing_fields':['writer_request_and_raw_output'],'trace_incomplete':True}}))
            stage='display_validation'
            if not self.continue_stage(job,{'progress':{'stage':'validation','matches_ready':True},'retrieval':bundle.get('retrieval',{})}if is_unified else None):return
            validation_started=time.monotonic()
            result,display=render_result(generated)
            if self.release:result['application_release']=self.release
            result['capability']=f['task'];result['answer_method']=method(result)
            if is_unified:result['progress']={'stage':'complete'}
            presentation_ms=(time.monotonic()-validation_started)*1000
            timings=result.setdefault('timings',{})
            # Reuse retains the historical observation in the family, but does not
            # charge earlier retrieval work again to this run's latency.
            if not f['bundle']and observation:
                for name,value in observation['stages'].items():
                    if timings.get(name)is None:timings[name]=value
            timings.update({'queue_wait_ms':queue_wait_ms,'family_freeze_elapsed_ms':freeze_ms,'evidence_cache_reused':bool(f['bundle']),'freeze_observation_scope':'retained_prior_family_freeze'if f['bundle']else'performed_this_run','freeze_observation_available':observation is not None,'presentation_validation_ms':presentation_ms,'first_useful_validated_response_ms':queue_wait_ms+(time.monotonic()-started)*1000,'first_useful_display_ms':None,'display_observation':'not_observed_here; ownership-checked browser delivery receipt required'})
            if is_unified:timings['first_returned_matches_ready_ms']=matches_ready_ms
            result['server_elapsed_seconds']=time.monotonic()-started
            with self.store.tx()as db:
                current=db.execute('SELECT *FROM jobs WHERE id=?',(job['id'],)).fetchone()
                if not current:return
                state='cancelled'if current['cancel_requested']else'complete'
                db.execute('UPDATE jobs SET state=?,finished=?WHERE id=?',(state,stamp(),job['id']))
                output,display_hash=(canonical(result),display) if state=='complete' else terminal_display(db,rid,state)
                db.execute('UPDATE runs SET state=?,result=?,display_hash=?,finished=?WHERE id=?',(state,output,display_hash,stamp(),rid))
        except Exception as error:
            record_stage(self.store,rid,'failure',{'stage':stage,'elapsed_ms':(time.monotonic()-started)*1000,**safe_exception(error)})
            code=error.code if isinstance(error,Failure)else'answer_unavailable'
            with self.store.tx()as db:
                current=db.execute('SELECT state,cancel_requested FROM jobs WHERE id=?',(job['id'],)).fetchone()
                if not current or current['state']!='running':return
                state='cancelled'if current['cancel_requested']else'failed';finished=stamp()
                db.execute('UPDATE jobs SET state=?,error_code=?,finished=?WHERE id=?',(state,code,finished,job['id']))
                output,display_hash=terminal_display(db,rid,state)
                db.execute('UPDATE runs SET state=?,result=?,display_hash=?,finished=?WHERE id=?',(state,output,display_hash,finished,rid))
            # Only an error code is retained; provider exceptions may contain private URLs or model identities.
        finally:
            from .cycles import monitor_canary
            try:monitor_canary(self.store)
            except Failure as error:
                if error.code!='cycle_busy':raise
    def loop(self,pool):
        while not self.stop_event.is_set():
            job=self.claim(pool)
            if job:self.execute(job)
            else:self.stop_event.wait(.15)
