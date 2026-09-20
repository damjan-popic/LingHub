"""Owned immutable families, render receipts and revisioned feedback."""
from __future__ import annotations
import json,secrets
from .store import canonical,digest,stamp,uid
from .security import Failure
from . import collation, editorial, unified
from .classification import method
from . import human_feedback
from .adoption import historical_record

RATINGS={None,'useful','partly_useful','not_useful'}
TAGS={'linguistic','missing_context','source','accent','language','interface','slow','incorrect','incomplete','unclear','too_long','too_short','source_issue','wrong_language','unsupported','other','wrong_form','missing_qualification','citation','style','translation','factual_error','irrelevant','unsafe','formatting'}
VOTES={'A','B','equivalent','both_inadequate','cannot_judge'}

def text(value,maximum=12000):
    if not isinstance(value,str)or len(value)>maximum or '\0'in value:raise Failure('invalid_request')
    try:value.encode('utf-8')
    except UnicodeError:raise Failure('invalid_request')from None
    return value

def checked_key(key):
    if not isinstance(key,str)or not 8<=len(key)<=100:raise Failure('invalid_idempotency_key')
    return key

def fields(payload,allowed):
    if not isinstance(payload,dict)or set(payload)-set(allowed):raise Failure('invalid_request')

class Domain:
    def __init__(self,store,tasks=('reference','source_search'),models=('qwen','gemma'),max_queue=30,per_owner=4,generation_admission=None,comparisons_enabled=True,unified_answers=False,release=None):
        self.store=store;self.tasks=tuple(tasks);self.models=tuple(models);self.max_queue=max_queue;self.per_owner=per_owner;self.generation_admission=generation_admission;self.comparisons_enabled=comparisons_enabled
        self.release=release
        self.unified_answers=unified_answers
        self.default_model='auto'if'auto'in self.models else'qwen'if'qwen'in self.models else self.models[0]
        self.comparison_models=tuple(model for model in self.models if model in {'qwen','gemma'})
    def require_generation(self,models):
        if self.generation_admission:self.generation_admission(models)
    def answer_actions(self,run):
        value=json.loads(run['result'])if run and run['result']else {}
        return method(value)not in {'clarification_required','capability_unavailable','failed'}
    def owned(self,db,table,identifier,owner):
        if table not in {'families','jobs','assignments','feedback'}:raise ValueError('Invalid owned table')
        row=db.execute(f'SELECT * FROM {table} WHERE id=? AND owner=?',(identifier,owner)).fetchone()
        if not row:raise Failure('not_found',404)
        if table=='families':historical_record(db,identifier,owner=owner)
        return dict(row)
    def run(self,db,identifier,owner):
        row=db.execute('SELECT r.* FROM runs r JOIN families f ON f.id=r.family_id WHERE r.id=? AND f.owner=?',(identifier,owner)).fetchone()
        if not row:raise Failure('not_found',404)
        historical_record(db,row['family_id'],owner=owner,run_id=identifier)
        return dict(row)
    def idem(self,db,owner,scope,payload,action):
        key=checked_key(payload.get('idempotency_key'));request_hash=digest({k:v for k,v in payload.items()if k!='idempotency_key'})
        row=db.execute('SELECT * FROM request_keys WHERE owner=?AND scope=?AND key=?',(owner,scope,key)).fetchone()
        if row:
            if row['payload_hash']!=request_hash:raise Failure('idempotency_conflict',409)
            return json.loads(row['response'])
        result=action();db.execute('INSERT INTO request_keys VALUES(?,?,?,?,?,?)',(owner,scope,key,request_hash,canonical(result),stamp()));return result
    def admit(self,db,owner,count=1):
        active="state IN ('queued','running')"
        if db.execute(f'SELECT count(*)FROM jobs WHERE {active}').fetchone()[0]+count>self.max_queue or db.execute(f'SELECT count(*)FROM jobs WHERE owner=?AND {active}',(owner,)).fetchone()[0]+count>self.per_owner:raise Failure('queue_full',429)
    def new_run(self,db,family,owner,tier,model,assignment=None,parent=None,automated=False):
        if historical_record(db,family,owner=owner)is not None:raise Failure('historical_generation_unavailable',409)
        rid,jid=uid(),uid();now=stamp()
        db.execute('INSERT INTO runs(id,family_id,tier,model,revision,parent_run_id,assignment_id,state,created)VALUES(?,?,?,?,1,?,?,?,?)',(rid,family,tier,model,parent,assignment,'queued',now))
        task=db.execute('SELECT task FROM families WHERE id=?',(family,)).fetchone()[0]
        job_model='cpu'if task in {'source_search','text_check','terminology','morphology',*editorial.SOURCES}else model
        db.execute('INSERT INTO jobs(id,run_id,owner,kind,model,state,created)VALUES(?,?,?,?,?,?,?)',(jid,rid,owner,tier,job_model,'queued',now))
        human_feedback.accepted(db,rid,owner,automated=automated,release=self.release)
        return {'run_id':rid,'job_id':jid}
    def select_policy(self,db,owner,comparison=False):
        baseline=self.store.meta('active_policy',db=db)
        if comparison or self.store.meta('adaptation_paused','0',db)=='1'or self.store.meta('human_testing_mode','0',db)=='1':return baseline
        row=db.execute("SELECT * FROM canaries WHERE state='running'AND expires>?ORDER BY created DESC LIMIT 1",(stamp(),)).fetchone()
        # Stable account/cohort allocation; no per-request reroll or existing-family mutation.
        if row and int(digest([owner,row['id']])[:8],16)%10==0:return row['candidate']
        return baseline
    def create_family(self,owner,payload):
        fields(payload,{'question','context','locale','interface_locale','input_language','focus','model','task','idempotency_key','origin','parent_run_id'})
        original_question=text(payload.get('question'),5000);q=original_question.strip();context=text(payload.get('context',''),3000)
        locale=payload.get('locale','sl');interface=payload.get('interface_locale',locale);input_language=payload.get('input_language','und');task=payload.get('task','reference');model=payload.get('model',self.default_model)
        if any(not isinstance(v,str)for v in (locale,interface,input_language,task,model)):raise Failure('invalid_request')
        if not q or locale not in {'sl','it'}or interface not in {'sl','it'}or input_language not in {'sl','it','mixed','und'}or task not in self.tasks or model not in self.models:raise Failure('invalid_request')
        checked_key(payload.get('idempotency_key'))
        if payload.get('origin') is not None and payload.get('origin')!='automated_test':raise Failure('invalid_request')
        focus=text(payload['focus'],512).strip()if 'focus'in payload else None
        if focus=='':focus=None
        clarification=None
        if task=='text_check'and input_language not in {'sl','it'}:raise Failure('input_language_required')
        if task=='morphology'and input_language not in {'sl','it'}:clarification={'code':'input_language_required','fields':['input_language']}
        if task=='reference'and not self.unified_answers:
            routing=collation.plan(q,input_language,focus)
            if routing and routing['status']=='clarification_required':
                language=routing['reason']=='input_language_required'
                clarification={'code':'input_language_required'if language else'focus_required','fields':['input_language'if language else'focus']}
        if clarification:return {'status':'clarification_required','clarification':clarification}
        if task in {'terminology',*editorial.SOURCES}and not 2<=len(q)<=500:raise Failure('invalid_request')
        if task=='morphology'and (len(q)>1000 or not 1<=len(q.split())<=32 or any(len(token)>200 for token in q.split())):raise Failure('invalid_request')
        with self.store.tx()as db:
            def action():
                if task=='reference'and not self.unified_answers:self.require_generation([model])
                self.admit(db,owner);fid=uid();policy=self.select_policy(db,owner)if task=='reference'else self.store.meta('active_policy',db=db)
                db.execute('INSERT INTO families VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,owner,q,context,task,locale,interface,input_language,model,policy,None,None,stamp()))
                db.execute('INSERT INTO family_inputs VALUES(?,?)',(fid,original_question))
                if focus:db.execute('INSERT INTO family_focus VALUES(?,?)',(fid,focus))
                parent=payload.get('parent_run_id')
                if parent:self.run(db,parent,owner) # Same-owner retry link only.
                r=self.new_run(db,fid,owner,'short',model,parent=parent,automated=payload.get('origin')=='automated_test');return dict(family_id=fid,**r)
            saved=self.idem(db,owner,'family',payload,action)
        return {'family':self.family(owner,saved['family_id']),'job':self.job(owner,saved['job_id'])}
    def expand(self,owner,fid,payload):
        with self.store.tx()as db:
            f=self.owned(db,'families',fid,owner)
            def action():
                old=db.execute("SELECT * FROM runs WHERE family_id=?AND tier='long'AND assignment_id IS NULL ORDER BY revision DESC LIMIT 1",(fid,)).fetchone()
                if old:return {'job_id':db.execute('SELECT id FROM jobs WHERE run_id=?',(old['id'],)).fetchone()[0]}
                parent=db.execute("SELECT * FROM runs WHERE family_id=?AND tier='short'AND assignment_id IS NULL ORDER BY revision DESC LIMIT 1",(fid,)).fetchone()
                if not parent or parent['state']!='complete' or f['task']!='reference'or not self.answer_actions(parent):raise Failure('expansion_unavailable',409)
                self.require_generation([f['model']])
                self.admit(db,owner);return self.new_run(db,fid,owner,'long',f['model'],parent=parent['id'])
            result=self.idem(db,owner,'expand:'+fid,payload,action)
        return {'family':self.family(owner,fid),'job':self.job(owner,result['job_id'])}
    def tier(self,db,run,blind=False):
        historical=historical_record(db,run['family_id'],run_id=run['id'])
        r=dict(run);out={k:r[k]for k in ('state','revision','parent_run_id')};out['run_id']=r['id']
        job=db.execute('SELECT id,error_code FROM jobs WHERE run_id=?',(r['id'],)).fetchone()
        if not blind:
            out['job_id']=job[0]if job else None
            if job and job['error_code']:out['error_code']=job['error_code']
        if r['result'] or r['state'] in {'failed','cancelled'}:
            locale=db.execute('SELECT locale FROM families WHERE id=?',(r['family_id'],)).fetchone()[0]
            result=human_feedback.visible_result(r,locale)
            for k in ('status','short_answer','answer_md','source_badges','evidence','blocks','limitations','correction','answer_support','answer_kind','generation_status','synthesis_mode','validation','quality_actions','citations','deterministic_result','clarification_code','capability','answer_method','retrieval','generation','progress'):
                if blind and k in {'retrieval','generation','progress'}:continue
                if k in result:out[k]=result[k]
            if not blind:
                out['display_sha256']=r['display_hash'] or digest(result.get('blocks',[]));out['model']=r['model']
                from .human_feedback import public_execution,provider_diagnostics
                out.update(public_execution(result))
                validation=provider_diagnostics(result).get('generation_validation')
                if validation:out['generation_validation']=validation
        if historical is not None:out['historical_evidence']={'status':'exact_retained_record','fresh_origin_assurance':False}
        # Public results are deliberately allowlisted. Signed bundles, profiles, timings and hashes stay server-side.
        return out
    def family(self,owner,fid):
        with self.store.connect()as db:
            f=self.owned(db,'families',fid,owner);out={'answer_family_id':fid,'question':f['question'],'locale':f['locale'],'input_language':f['input_language'],'task':f['task'],'model':f['model'],'created':f['created'],'short':None,'long':None}
            bundle=json.loads(f['bundle'])if f['bundle']else{}
            out['matches_available']=bundle.get('contract')==unified.CONTRACT
            out['unified_answers']=out['matches_available']or(self.unified_answers and f['task']=='reference'and not f['bundle'])
            focus=db.execute('SELECT focus FROM family_focus WHERE family_id=?',(fid,)).fetchone()
            if focus:out['focus']=focus[0]
            original=db.execute('SELECT original_question FROM family_inputs WHERE family_id=?',(fid,)).fetchone()
            if original:out['question']=original[0]
            for r in db.execute('SELECT * FROM runs WHERE family_id=?AND assignment_id IS NULL ORDER BY revision',(fid,)):out[r['tier']]=self.tier(db,r)
            return out
    def families(self,owner):
        with self.store.connect()as db:ids=[r[0]for r in db.execute('SELECT id FROM families WHERE owner=?ORDER BY created DESC LIMIT 100',(owner,))]
        return {'families':[self.family(owner,fid)for fid in ids]}
    def matches(self,owner,fid,offset=0,limit=20,source=None,summary=False):
        with self.store.connect()as db:
            f=self.owned(db,'families',fid,owner)
            bundle=json.loads(f['bundle'])if f['bundle']else{}
            if bundle.get('contract')!=unified.CONTRACT:raise Failure('not_found',404)
            latest={}
            for row in db.execute("SELECT tier,result FROM runs WHERE family_id=?AND assignment_id IS NULL ORDER BY revision",(fid,)):
                latest[row['tier']]=json.loads(row['result'])if row['result']else{}
            usage_final=not bool(db.execute("SELECT 1 FROM runs WHERE family_id=?AND assignment_id IS NULL AND state IN ('queued','running') LIMIT 1",(fid,)).fetchone())
            if source is not None or summary:
                generated=[r for r in latest.values()if r.get('answer_kind')in {'generated','mixed'}]
                cited={unified.evidence_id(e)for result in generated for e in result.get('citations',[])if isinstance(e,dict)}
                cited.update(eid for result in generated for b in result.get('blocks',[])for eid in b.get('evidence_ids',[]))
                page=(unified.source_summary(bundle,cited,usage_final)if summary else
                      unified.source_records_page(bundle,cited,source,offset,limit,usage_final))
                page['answer_status']='pending'if not usage_final else'generated'if generated else'no_support'
                return page
            used={unified.evidence_id(e)for result in latest.values()for e in result.get('evidence',[])if isinstance(e,dict)}
            page=unified.matches_page(bundle,used,offset,limit)
            page['usage_final']=usage_final
            return page
    def job(self,owner,jid):
        with self.store.connect()as db:
            j=self.owned(db,'jobs',jid,owner);r=self.run(db,j['run_id'],owner)
            if r['assignment_id']:
                a=self.owned(db,'assignments',r['assignment_id'],owner)
                terminal=db.execute("SELECT count(*)FROM runs WHERE assignment_id=?AND tier=?AND state IN ('complete','failed','cancelled')",(a['id'],r['tier'])).fetchone()[0]
                if a['blind']and not a['revealed']and terminal<2:return {'job_id':jid,'state':'queued','family_id':r['family_id'],'tier':r['tier'],'position':None,'error_code':None,'pair_state':'preparing'}
            result=json.loads(r['result'])if r['result']else{}
            cancellation_mode='queued_removed'if j['state']=='cancelled'and j['started']is None else'stop_before_generation'if result.get('progress',{}).get('stage')=='retrieval'else'finish_current_operation'
            return {'job_id':jid,'state':j['state'],'family_id':r['family_id'],'tier':r['tier'],'model':r['model'],'position':db.execute("SELECT count(*)FROM jobs WHERE state='queued'AND created<?",(j['created'],)).fetchone()[0]+1 if j['state']=='queued'else None,'error_code':j['error_code'],'progress':result.get('progress'),'cancel_requested':bool(j['cancel_requested']),'cancellation_mode':cancellation_mode if j['cancel_requested']else None,'provider_interruption_supported':False}
    def cancel(self,owner,jid):
        with self.store.tx()as db:
            j=self.owned(db,'jobs',jid,owner)
            if j['state']=='queued':
                db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished=?WHERE id=?",(stamp(),jid));display,display_hash=human_feedback.terminal_display(db,j['run_id'],'cancelled');db.execute("UPDATE runs SET state='cancelled',result=?,display_hash=?,finished=?WHERE id=?",(display,display_hash,stamp(),j['run_id']))
            elif j['state']=='running':db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?',(jid,))
        return self.job(owner,jid)
    def delivered(self,owner,rid,payload):
        with self.store.tx()as db:
            r=self.run(db,rid,owner)
            locale=db.execute('SELECT locale FROM families WHERE id=?',(r['family_id'],)).fetchone()[0]
            expected=r['display_hash'] or digest(human_feedback.visible_result(r,locale).get('blocks',[]))
            if r['state'] not in {'complete','failed','cancelled'} or payload.get('display_sha256')!=expected:raise Failure('display_changed',409)
            if r['assignment_id']:
                a=self.owned(db,'assignments',r['assignment_id'],owner)
                if a['blind']and not a['revealed']:raise Failure('blind_delivery_route_required',409)
            self.mark_delivery(db,owner,r,payload.get('expanded',False))
        return {'acknowledged':True}
    def mark_delivery(self,db,owner,r,expanded):
        if r['state'] not in {'complete','failed','cancelled'}:raise Failure('answer_not_ready',409)
        locale=db.execute('SELECT locale FROM families WHERE id=?',(r['family_id'],)).fetchone()[0]
        display=r['display_hash'] or digest(human_feedback.visible_result(r,locale).get('blocks',[]))
        db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',(uid(),owner,r['id'],display,int(bool(expanded)),stamp()))
        db.execute('UPDATE runs SET delivered=coalesce(delivered,?)WHERE id=?',(stamp(),r['id']))
    def create_comparison(self,owner,payload):
        if not self.comparisons_enabled or self.store.meta('human_testing_mode','0')=='1':raise Failure('comparisons_deferred',409)
        fields(payload,{'family_id','blind','tier','idempotency_key'})
        if not isinstance(payload.get('family_id'),str):raise Failure('invalid_request')
        fid=payload.get('family_id');blind=payload.get('blind',True)
        if type(blind)is not bool or payload.get('tier','short')!='short' or set(self.comparison_models)!={'qwen','gemma'}:raise Failure('comparison_unavailable')
        with self.store.tx()as db:
            f=self.owned(db,'families',fid,owner)
            if f['task']!='reference':raise Failure('comparison_unavailable')
            def action():
                parent=db.execute("SELECT *FROM runs WHERE family_id=?AND tier='short'AND assignment_id IS NULL ORDER BY revision DESC LIMIT 1",(fid,)).fetchone()
                if parent and parent['state']=='complete'and not self.answer_actions(parent):raise Failure('comparison_unavailable',409)
                self.require_generation(self.comparison_models)
                self.admit(db,owner,2);aid=uid();mapping=dict(zip(('A','B'),secrets.SystemRandom().sample(list(self.comparison_models),2)))
                prior=bool(db.execute('SELECT 1 FROM runs WHERE family_id=?AND assignment_id IS NULL AND delivered IS NOT NULL',(fid,)).fetchone()or db.execute('SELECT 1 FROM assignments a JOIN runs r ON r.assignment_id=a.id WHERE a.family_id=?AND (a.revealed IS NOT NULL OR a.blind=0)AND r.delivered IS NOT NULL',(fid,)).fetchone())
                db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?,?,?,?)',(aid,owner,fid,int(blind),canonical(mapping),None,int(prior),'short',stamp()))
                for side,model in mapping.items():
                    run=self.new_run(db,fid,owner,'short',model,aid);db.execute('INSERT INTO assignment_runs VALUES(?,?,?,?)',(aid,'short',side,run['run_id']))
                return {'comparison_id':aid}
            out=self.idem(db,owner,'comparison',payload,action)
        return self.comparison(owner,out['comparison_id'])
    def comparison(self,owner,aid):
        with self.store.connect()as db:
            a=self.owned(db,'assignments',aid,owner);hidden=a['blind']and not a['revealed'];tiers={'short':{},'long':{}}
            for r in db.execute('SELECT r.*,ar.side FROM assignment_runs ar JOIN runs r ON r.id=ar.run_id WHERE ar.assignment_id=?',(aid,)):
                tiers[r['tier']][r['side']]=self.tier(db,r,hidden)
                if not hidden:tiers[r['tier']][r['side']]['model']=r['model']
            if hidden:
                for tier,sides in tiers.items():
                    if len(sides)!=2 or any(r['state']not in {'complete','failed','cancelled'}for r in sides.values()):tiers[tier]={side:{'state':'queued'}for side in ('A','B')}
            pair_ready=all(r.get('run_id')for r in tiers[a['active_tier']].values())
            vote=db.execute('SELECT choice,tier,revision,expanded FROM votes WHERE assignment_id=?ORDER BY revision DESC LIMIT 1',(aid,)).fetchone()
            return {'comparison_id':aid,'family_id':a['family_id'],'blind':bool(a['blind']),'revealed':bool(a['revealed']),'tier':a['active_tier'],'pair_state':'ready'if pair_ready else'preparing','sides':tiers[a['active_tier']],'short':tiers['short'],'long':tiers['long'],'vote':dict(vote)if vote else None,'prior_named_exposure':bool(a['prior_named_exposure'])}
    def expand_comparison(self,owner,aid,payload):
        if not self.comparisons_enabled or self.store.meta('human_testing_mode','0')=='1':raise Failure('comparisons_deferred',409)
        with self.store.tx()as db:
            a=self.owned(db,'assignments',aid,owner)
            def action():
                if a['active_tier']=='long':return {'comparison_id':aid}
                short=list(db.execute("SELECT ar.side,r.* FROM assignment_runs ar JOIN runs r ON r.id=ar.run_id WHERE ar.assignment_id=?AND ar.tier='short'",(aid,)))
                if len(short)!=2 or any(r['state']!='complete'or not self.answer_actions(r)for r in short):raise Failure('expansion_unavailable',409)
                self.require_generation([r['model']for r in short])
                self.admit(db,owner,2)
                for r in short:
                    run=self.new_run(db,a['family_id'],owner,'long',r['model'],aid,r['id']);db.execute('INSERT INTO assignment_runs VALUES(?,?,?,?)',(aid,'long',r['side'],run['run_id']))
                db.execute("UPDATE assignments SET active_tier='long'WHERE id=?",(aid,));return {'comparison_id':aid}
            self.idem(db,owner,'comparison-expand:'+aid,payload,action)
        return self.comparison(owner,aid)
    def comparison_delivery(self,owner,aid,payload):
        tier=payload.get('tier','short')
        if not isinstance(tier,str)or tier not in {'short','long'}:raise Failure('invalid_request')
        with self.store.tx()as db:
            self.owned(db,'assignments',aid,owner)
            rows=list(db.execute('SELECT r.*FROM assignment_runs ar JOIN runs r ON r.id=ar.run_id WHERE ar.assignment_id=?AND ar.tier=?',(aid,tier)))
            if len(rows)!=2:raise Failure('answer_not_ready',409)
            for r in rows:self.mark_delivery(db,owner,r,tier=='long')
        return {'acknowledged':True}
    def vote(self,owner,aid,payload):
        choice=payload.get('choice');tier=payload.get('tier','short')
        if not isinstance(choice,str)or not isinstance(tier,str):raise Failure('invalid_request')
        if choice not in VOTES or tier not in {'short','long'}:raise Failure('invalid_request')
        with self.store.tx()as db:
            self.owned(db,'assignments',aid,owner)
            def action():
                runs=list(db.execute('SELECT r.*FROM assignment_runs ar JOIN runs r ON r.id=ar.run_id WHERE ar.assignment_id=?AND ar.tier=?',(aid,tier)))
                if any(not self.answer_actions(r)for r in runs):raise Failure('feedback_unavailable',409)
                ready=db.execute('SELECT count(*)FROM assignment_runs ar JOIN runs r ON r.id=ar.run_id WHERE ar.assignment_id=?AND ar.tier=?AND r.delivered IS NOT NULL',(aid,tier)).fetchone()[0]
                if ready!=2:raise Failure('answer_not_delivered',409)
                revision=db.execute('SELECT coalesce(max(revision),0)+1 FROM votes WHERE assignment_id=?',(aid,)).fetchone()[0]
                db.execute('INSERT INTO votes(id,assignment_id,owner,revision,tier,choice,expanded,created)VALUES(?,?,?,?,?,?,?,?)',(uid(),aid,owner,revision,tier,choice,int(tier=='long'),stamp()));return {'acknowledged':True,'revision':revision}
            return self.idem(db,owner,'vote:'+aid,payload,action)
    def reveal(self,owner,aid):
        with self.store.tx()as db:
            self.owned(db,'assignments',aid,owner)
            if not db.execute('SELECT 1 FROM votes WHERE assignment_id=?',(aid,)).fetchone():raise Failure('vote_required',409)
            db.execute('UPDATE assignments SET revealed=coalesce(revealed,?)WHERE id=?',(stamp(),aid))
        return self.comparison(owner,aid)
    def feedback(self,owner,payload):
        fields(payload,{'run_id','rating','quality_score','display_locale','tags','comment','proposed_correction','anchor','target','idempotency_key','feedback_id'})
        if not isinstance(payload.get('run_id'),str):raise Failure('invalid_request')
        score=payload.get('quality_score');display_locale=payload.get('display_locale')
        if score is not None and(type(score)is not int or not 1<=score<=5):raise Failure('invalid_feedback')
        if display_locale is not None and display_locale not in {'sl','it'}:raise Failure('invalid_feedback')
        rating=payload.get('rating');tags=payload.get('tags',[]);comment=text(payload.get('comment',''),8000);correction=text(payload.get('proposed_correction',''),8000);anchor=payload.get('anchor');target=payload.get('target',{'kind':'answer'})
        if rating is not None and not isinstance(rating,str):raise Failure('invalid_feedback')
        if rating not in RATINGS or not isinstance(tags,list)or len(tags)>12 or any(not isinstance(t,str)or t not in TAGS for t in tags)or(not rating and score is None and not(tags or anchor or comment or correction)):raise Failure('invalid_feedback')
        if not isinstance(target,dict)or set(target)-{'kind','evidence_id'}or not isinstance(target.get('kind'),str)or target.get('kind')not in {'answer','source'}:raise Failure('invalid_feedback')
        with self.store.tx()as db:
            r=self.run(db,payload.get('run_id'),owner)
            if r['state'] not in {'complete','failed','cancelled'} or not r['delivered']:raise Failure('answer_not_delivered',409)
            locale=db.execute('SELECT locale FROM families WHERE id=?',(r['family_id'],)).fetchone()[0]
            result=human_feedback.visible_result(r,locale);evidence=result.get('evidence',[])
            if result.get('answer_kind') in {'clarification','clarification_required'}:raise Failure('feedback_unavailable',409)
            r['display_hash']=r['display_hash'] or digest(result.get('blocks',[]))
            task=db.execute('SELECT task FROM families WHERE id=?',(r['family_id'],)).fetchone()[0]
            target=dict(target,capability=task,answer_method=result.get('answer_method',method(result)))
            if target['kind']=='source'and not any(e.get('evidence_id',e.get('passage_id',e.get('citation_id')))==target.get('evidence_id')for e in evidence):raise Failure('invalid_evidence')
            if anchor:
                if not isinstance(anchor,dict):raise Failure('invalid_anchor')
                block=next((b for b in result['blocks']if b['block_id']==anchor.get('block_id')),None)
                start,end=anchor.get('start'),anchor.get('end')
                if not block or type(start)is not int or type(end)is not int or not 0<=start<end<=len(block['text'])or block['text'][start:end]!=anchor.get('quote'):raise Failure('invalid_anchor')
                before=text(anchor.get('prefix',''),100);after=text(anchor.get('suffix',''),100)
                if not block['text'][:start].endswith(before)or not block['text'][end:].startswith(after):raise Failure('invalid_anchor')
                anchor={k:anchor.get(k)for k in ('block_id','start','end','quote','prefix','suffix')}
            def action():
                old=db.execute('SELECT *FROM feedback WHERE owner=?AND run_id=?',(owner,r['id'])).fetchone();fid=old['id']if old else uid();revision=old['revision']+1 if old else 1
                if payload.get('feedback_id')and payload['feedback_id']!=fid:raise Failure('not_found',404)
                if not old:db.execute('INSERT INTO feedback VALUES(?,?,?,0)',(fid,owner,r['id']))
                db.execute('UPDATE feedback SET revision=?WHERE id=?',(revision,fid))
                event_id=uid();created=stamp()
                db.execute('INSERT INTO feedback_events(id,feedback_id,revision,rating,tags,comment,correction,anchor,display_hash,created,target)VALUES(?,?,?,?,?,?,?,?,?,?,?)',(event_id,fid,revision,rating,canonical(tags),comment,correction,canonical(anchor)if anchor else None,r['display_hash'],created,canonical(target)))
                locale=display_locale or db.execute('SELECT interface_locale FROM families WHERE id=?',(r['family_id'],)).fetchone()[0]
                snapshot=human_feedback.observation(db,owner,r,result,created,locale)
                db.execute('INSERT INTO feedback_observations VALUES(?,?,?)',(event_id,score,canonical(snapshot)))
                return {'feedback_id':fid,'revision':revision,'acknowledged':True}
            return self.idem(db,owner,'feedback',payload,action)
    def feedback_history(self,owner=None,reviewer=None):
        with self.store.connect()as db:
            sql='SELECT f.id AS feedback_id,f.run_id,f.revision,e.id,e.rating,e.tags,e.comment,e.correction,e.anchor,e.created,e.target FROM feedback f JOIN feedback_events e ON e.feedback_id=f.id AND e.revision=f.revision'
            args=[]
            if owner:sql+=' WHERE f.owner=?';args=[owner]
            elif reviewer:sql+=' JOIN review_assignments a ON a.feedback_id=f.id WHERE a.reviewer=?';args=[reviewer]
            rows=[dict(r)for r in db.execute(sql+' ORDER BY e.created DESC LIMIT 500',args)]
            for r in rows:
                human_feedback.attach(db,r)
                for k in ('tags','anchor','target'):r[k]=json.loads(r[k])if r[k]else None
            return {'feedback':rows}
