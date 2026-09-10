"""Deterministic private analysis and narrowly bounded, independently gated policies."""
from __future__ import annotations
from collections import Counter,defaultdict
from contextlib import contextmanager
import fcntl,json,math,os
from pathlib import Path
from .store import canonical,digest,stamp,BASE_POLICY
from .security import Failure

VERSION='pilot-analysis-v1'
ALLOWLIST={'rerank_policy':('always_for_answers','conditional_for_answers'),'short_style_preset':('direct_qualified_v1','direct_examples_v1'),'long_style_preset':('explanation_examples_v1','explanation_comparison_v1')}

@contextmanager
def cycle_lock(store):
    path=store.path.with_suffix('.cycles.lock');fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'a')as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Failure('cycle_busy',409)from None
        yield

def technical_failure(row):
    return row['state']=='failed'or json.loads(row.get('result')or'{}').get('status')=='failed'

def percentile(values,q=.95):
    values=sorted(values)
    if not values:return None
    return values[max(0,math.ceil(q*len(values))-1)]

def snapshot(store,previous=0):
    with store.tx()as db:
        sequence=db.execute("SELECT seq FROM sqlite_sequence WHERE name='feedback_events'").fetchone();cutoff=sequence[0]if sequence else 0
        rows=[dict(r)for r in db.execute('''SELECT e.*,f.owner,f.run_id,a.origin,a.cohort,r.model,r.tier,r.result,r.family_id,fa.locale,fa.task,fa.identity,fa.policy_id FROM feedback_events e JOIN feedback f ON f.id=e.feedback_id JOIN accounts a ON a.id=f.owner JOIN runs r ON r.id=f.run_id JOIN families fa ON fa.id=r.family_id WHERE e.seq>?AND e.seq<=?AND e.revision=(SELECT max(e2.revision)FROM feedback_events e2 WHERE e2.feedback_id=e.feedback_id AND e2.seq<=?) ORDER BY e.seq''',(previous,cutoff,cutoff))]
        # Denominators include actual delivered answers even when no vote was supplied.
        run_rows=[dict(r)for r in db.execute('SELECT r.id,r.state,r.delivered,r.finished,r.result,r.model,r.tier,f.locale,f.task,a.origin FROM runs r JOIN families f ON f.id=r.family_id JOIN accounts a ON a.id=f.owner')]
    return cutoff,rows,run_rows

def environment_pin(rows):
    return digest({'identities':sorted({r['identity']or'{}'for r in rows}),
                   'cohorts':sorted({r['cohort']for r in rows})})

def development_groups(rows):
    result=set()
    for row in rows:
        for item in json.loads(row['result']or'{}').get('evidence',[]):
            source=item.get('external_id',item.get('source_id'))
            if source:result.add(str(item.get('source_slug',''))+':'+str(source))
    return result

def suggestions(rows,base):
    output=[];slices=defaultdict(list)
    for row in rows:
        if row['origin']=='human'and row['task']=='reference':slices[row['locale']].append(row)
    for locale,items in sorted(slices.items()):
        if len({r['family_id']for r in items})<10 or len({r['owner']for r in items})<3:continue
        counts=Counter(t for r in items for t in json.loads(r['tags']))
        choices=[]
        if counts['missing_context']+counts['incomplete']>=2 and base['context_passage_limit']<16:
            choices.append(('context_passage_limit',min(16,base['context_passage_limit']+2),'missing_context'))
        if counts['unclear']+counts['style']+counts['interface']>=2:
            choices.append(('long_style_preset','explanation_comparison_v1'if base['long_style_preset']=='explanation_examples_v1'else'explanation_examples_v1','clarity'))
        if counts['too_short']>=2:
            choices.append(('short_style_preset','direct_examples_v1'if base['short_style_preset']=='direct_qualified_v1'else'direct_qualified_v1','short_explanation'))
        for key,value,reason in choices:
            config=dict(base,**{key:value});output.append({'config':config,'target_locale':locale,'reason':reason,'observed_tag_counts':dict(counts),'policy_id':digest(config)})
            if len(output)==3:return output
    return output

def iter_policy(store,policy):
    with store.connect()as db:yield db.execute('SELECT config FROM policies WHERE id=?',(policy,)).fetchone()[0]

def analyse(store,output_dir=None):
    with cycle_lock(store):
        cutoff,rows,runs=snapshot(store);policy=store.meta('active_policy')
        with store.connect()as db:
            votes=[dict(r)for r in db.execute('''SELECT v.*,a.origin,f.locale,asgn.prior_named_exposure,asgn.blind,asgn.revealed FROM votes v JOIN accounts a ON a.id=v.owner JOIN assignments asgn ON asgn.id=v.assignment_id JOIN families f ON f.id=asgn.family_id WHERE v.revision=(SELECT max(v2.revision)FROM votes v2 WHERE v2.assignment_id=v.assignment_id AND v2.tier=v.tier)ORDER BY v.seq''')]
        cid=digest({'kind':'analysis','cutoff':cutoff,'feedback_ids':[{k:r[k]for k in ('id','revision','seq')}for r in rows],'runs':[{k:r[k]for k in ('id','state','delivered','finished')}for r in runs],'votes':[{k:v[k]for k in ('id','revision','seq')}for v in votes],'policy':policy,'analyser':VERSION})
        with store.connect()as db:old=db.execute('SELECT report FROM cycles WHERE id=?',(cid,)).fetchone()
        if old:
            report=json.loads(old[0])
            if output_dir:write_analysis(report,output_dir)
            return report
        by_origin=Counter(r['origin']for r in rows);human=[r for r in rows if r['origin']=='human'];groups=defaultdict(lambda:Counter());issues=[]
        for row in rows:
            key=canonical([row['origin'],row['locale'],row['task'],row['model'],row['tier']]);groups[key]['feedback']+=1;groups[key][row['rating']or'issue_only']+=1
            for tag in json.loads(row['tags']):groups[key]['tag:'+tag]+=1
            if row['rating']!='useful'or row['tags']!='[]':
                issues.append({'triage_id':digest([row['feedback_id'],row['revision']])[:24],'feedback_id':row['feedback_id'],'revision':row['revision'],'origin':row['origin'],'locale':row['locale'],'task':row['task'],'tier':row['tier'],'tags':json.loads(row['tags']),'target':json.loads(row['target']),'comment_is_untrusted_data':True})
        latencies=[];failures=0;delivered=0
        for row in runs:
            if row['origin']!='human':continue
            failures+=technical_failure(row);delivered+=bool(row['delivered'])
            result=json.loads(row['result'])if row['result']else{}
            value=result.get('server_elapsed_seconds')
            if isinstance(value,(int,float))and math.isfinite(value):latencies.append(value)
        vote_groups=defaultdict(Counter)
        for vote in votes:
            key=canonical([vote['origin'],vote['locale'],vote['tier'],'blind'if vote['blind']else'named',bool(vote['prior_named_exposure'])]);vote_groups[key][vote['choice']]+=1
        source_groups=defaultdict(Counter)
        for row in rows:
            sources={e.get('source_slug','unknown')for e in json.loads(row['result']or'{}').get('evidence',[])}or{'none'}
            for source in sources:
                key=canonical([row['origin'],source,row['locale'],row['task'],row['model'],row['tier']]);source_groups[key]['feedback']+=1;source_groups[key][row['rating']or'issue_only']+=1
        counts={'vote_groups':dict(vote_groups),'human_votes':sum(v['origin']=='human'for v in votes),'source_groups':dict(source_groups),'feedback_by_origin':dict(by_origin),'human_feedback':len(human),'human_participants':len({r['owner']for r in human}),'human_cases':len({r['family_id']for r in human}),'human_runs':sum(r['origin']=='human'for r in runs),'human_delivered_runs':delivered,'human_technical_failures':failures,'human_p95_seconds':percentile(latencies),'groups':dict(groups)}
        report={'schema':'klikai.pilot.analysis.v1','cycle_id':cid,'cutoff':cutoff,'policy_id':policy,'analyser_revision':VERSION,'status':'ANALYSIS_COMPLETE','input_hash':digest([{k:r[k]for k in ('id','revision','seq')}for r in rows]),'counts':counts,'issues':issues,'candidate_suggestions':suggestions(rows,json.loads(next(iter_policy(store,policy)))),'environment_pin':environment_pin(human),'development_source_groups':sorted(development_groups(human)),'reports':{'sl':f"Pilot: {len(human)} povratnih informacij ljudi; {delivered} prikazanih odgovorov. Sintetični podatki so ločeni. Samodejna sprememba zahteva neodvisno preverjanje.",'it':f"Pilot: {len(human)} riscontri umani; {delivered} risposte mostrate. Dati sintetici separati. Le modifiche automatiche richiedono una verifica indipendente."},'comments_sent_to_model':False}
        with store.tx()as db:db.execute('INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?)',(cid,'analysis',cutoff,0,report['input_hash'],report['status'],canonical(report),stamp(),stamp()))
        if output_dir:write_analysis(report,output_dir)
        return report

def private_write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():raise ValueError('Private output must not be a symlink')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w')as out:out.write(data);out.flush();os.fsync(out.fileno())

def write_analysis(report,directory):
    root=Path(directory)/report['cycle_id'];root.mkdir(parents=True,exist_ok=True,mode=0o700)
    payloads={'counts.json':canonical(report['counts'])+'\n','issues.jsonl':''.join(canonical(x)+'\n'for x in report['issues']),'candidate-suggestions.json':canonical(report['candidate_suggestions'])+'\n','report-sl.md':report['reports']['sl']+'\n','report-it.md':report['reports']['it']+'\n','receipt.json':canonical(report)+'\n'}
    for name,data in payloads.items():
        p=root/name
        if p.exists():
            if p.read_text()!=data:raise Failure('analysis_output_changed',409)
        else:private_write(p,data)

def validate_candidate(base,candidate,implemented=('context_passage_limit','rerank_policy','short_style_preset','long_style_preset')):
    if not isinstance(candidate,dict)or set(candidate)!=set(BASE_POLICY):raise Failure('policy_not_allowlisted')
    for key,allowed in ALLOWLIST.items():
        if candidate[key]not in allowed:raise Failure('policy_not_allowlisted')
    lexical=candidate['lexical_weight'];passages=candidate['context_passage_limit']
    if type(lexical)not in {int,float}or not math.isfinite(lexical)or not .35<=lexical<=.75 or abs(lexical-base['lexical_weight'])>.05000000001:raise Failure('policy_not_allowlisted')
    if type(passages)is not int or not 6<=passages<=16 or abs(passages-base['context_passage_limit'])>2:raise Failure('policy_not_allowlisted')
    changes={k for k in base if base[k]!=candidate[k]}
    if not changes or changes-set(implemented):raise Failure('policy_not_implemented')
    return digest(candidate)

def independent_gate(validation,baseline,candidate,development_families,expected_environment=None,dev_groups=None):
    """Operator-supplied source-reviewed paired evidence, never model self-grading."""
    if not isinstance(validation,dict):return False,'independent_validation_missing',{}
    required={'source_reviewed':True,'independent_of_development':True,'deterministic_gate_passed':True,'regression_replay_passed':True,'development_replay_passed':True}
    if any(validation.get(k)is not v for k,v in required.items())or validation.get('reviewer_kind')not in {'human','independent_source_review'}or not validation.get('reviewer_id'):return False,'independent_validation_missing',{}
    if validation.get('baseline_policy_id')!=baseline or validation.get('candidate_policy_id')!=candidate:return False,'validation_pin_mismatch',{}
    if expected_environment is not None and validation.get('source_model_profile_cohort_pin')!=expected_environment:return False,'environment_pin_mismatch',{}
    pairs=validation.get('pairs',[])
    if not isinstance(pairs,list)or len(pairs)<30:return False,'insufficient_reviewed_pairs',{}
    ids=set();bg=[];cg=[];bt=[];ct=[];critical=0
    try:
        for p in pairs:
            if not isinstance(p,dict)or not p.get('source_group')or not p.get('case_id')or p['case_id']in ids or p.get('family_id')in development_families or p.get('origin')!='human_reviewed_holdout' or not isinstance(p.get('source_review_hash'),str)or len(p['source_review_hash'])!=64 or any(c not in'0123456789abcdef'for c in p['source_review_hash'])or p['source_group']in(dev_groups or set()):return False,'holdout_overlap_or_unreviewed',{}
            ids.add(p['case_id'])
            for key,out,low,high in [('baseline_score',bg,0,1),('candidate_score',cg,0,1),('baseline_seconds',bt,.000001,100000),('candidate_seconds',ct,.000001,100000)]:
                v=p[key]
                if type(v)not in {int,float}or not math.isfinite(v)or not low<=v<=high:raise ValueError('Invalid measure')
                out.append(v)
            critical+=int(bool(p.get('critical_regression')))
    except (KeyError,TypeError,ValueError):return False,'invalid_validation',{}
    gain=sum(cg)/len(cg)-sum(bg)/len(bg);ratio=percentile(ct)/percentile(bt)
    metrics={'pairs':len(pairs),'score_gain':gain,'p95_ratio':ratio,'critical_regressions':critical,'source_groups':len({p['source_group']for p in pairs}),'engineering_gate_not_statistical_proof':True}
    if critical or gain<.02-1e-12 or ratio>1.1:return False,'validation_gate_failed',metrics
    return True,'validated',metrics

def adapt(store,candidates=None,validations=None,implemented=None):
    with cycle_lock(store):
        with store.connect()as db:
            previous=db.execute("SELECT coalesce(max(cutoff),0)FROM cycles WHERE kind='adaptation'").fetchone()[0];base_id=store.meta('active_policy',db=db);base=json.loads(db.execute('SELECT config FROM policies WHERE id=?',(base_id,)).fetchone()[0])
        cutoff,rows,runs=snapshot(store,previous);human=[r for r in rows if r['origin']=='human'and r['task']=='reference'];input_hash=digest([{k:r[k]for k in ('id','revision','seq')}for r in human]);cid=digest({'kind':'adaptation','cutoff':cutoff,'base':base_id,'analyser':VERSION})
        with store.connect()as db:old=db.execute('SELECT report FROM cycles WHERE id=?',(cid,)).fetchone()
        if old:return json.loads(old[0])
        report={'schema':'klikai.pilot.adaptation.v1','cycle_id':cid,'cutoff':cutoff,'previous_cutoff':previous,'input_hash':input_hash,'status':'ANALYSIS_ONLY_NO_VALIDATED_PROMOTION','human_events':len(human),'all_new_human_events':sum(r['origin']=='human'for r in rows),'eligible_task':'reference','participants':len({r['owner']for r in human}),'cases':len({r['family_id']for r in human}),'candidates':[],'baseline_policy_id':base_id}
        report['source_model_profile_cohort_pin']=environment_pin(human)
        if candidates is None:candidates=suggestions(human,base)
        if validations is None:
            validations={}
            for candidate in candidates:
                pid=digest(candidate.get('config',candidate));path=store.path.parent/'validation-inbox'/(pid+'.json')
                if path.exists():
                    if path.is_symlink()or path.stat().st_mode&0o077 or path.stat().st_uid!=os.geteuid():raise Failure('validation_input_not_private')
                    validations[pid]=json.loads(path.read_text())
        ready=len(human)>=20 and report['participants']>=3 and report['cases']>=10
        if store.meta('adaptation_paused','0')=='1':report['reason']='paused'
        elif not ready:report.update(reason='insufficient_new_human_data',status='INSUFFICIENT_DATA',promotion_status='ANALYSIS_ONLY_NO_VALIDATED_PROMOTION')
        else:
            slices=defaultdict(list)
            for r in human:slices[r['locale']].append(r)
            eligible={lang for lang,items in slices.items()if len({r['family_id']for r in items})>=10 and len({r['owner']for r in items})>=3}
            if not candidates:report['reason']='no_reviewed_candidate'
            if candidates and len(candidates)>3:raise Failure('too_many_candidates')
            for candidate in(candidates or[]):
                params=candidate.get('config',candidate);target=candidate.get('target_locale')
                item={'status':'rejected'}
                try:pid=validate_candidate(base,params,implemented)if implemented is not None else validate_candidate(base,params)
                except Failure as e:item['reason']=e.code;report['candidates'].append(item);continue
                item['policy_id']=pid
                if target and target not in eligible:item['reason']='target_slice_insufficient';report['candidates'].append(item);continue
                valid,reason,metrics=independent_gate((validations or{}).get(pid),base_id,pid,{r['family_id']for r in human},environment_pin(human),development_groups(human));item.update(reason=reason,metrics=metrics);report['candidates'].append(item)
                if valid and not report.get('candidate_policy_id'):
                    report.update(candidate_policy_id=pid,candidate_config=params,status='CANARY_PENDING',reason='independent_validation_passed');item['status']='selected'
        with store.tx()as db:
            # A running canary keeps its one baseline/candidate pair until observed or reverted.
            if report.get('candidate_policy_id')and db.execute("SELECT 1 FROM canaries WHERE state='running'").fetchone():report.pop('candidate_policy_id');report.pop('candidate_config');report.update(status='ANALYSIS_ONLY_NO_VALIDATED_PROMOTION',reason='canary_already_running')
            db.execute('INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?)',(cid,'adaptation',cutoff,previous,input_hash,report['status'],canonical(report),stamp(),stamp()))
            if report.get('candidate_policy_id'):
                pid=report['candidate_policy_id'];db.execute('INSERT OR IGNORE INTO policies VALUES(?,?,?,?,?)',(pid,canonical(report['candidate_config']),base_id,stamp(),'candidate'))
                human_runs=[r for r in runs if r['origin']=='human'];baseline_error=sum(technical_failure(r)for r in human_runs)/max(1,len(human_runs))
                db.execute('INSERT INTO canaries VALUES(?,?,?,?,?,?,?,?,?,?)',(digest([cid,pid]),cid,base_id,pid,'running',stamp(),stamp()+48*3600,0,baseline_error,0))
        return report

def monitor_canary(store):
    with cycle_lock(store),store.tx()as db:
        row=db.execute("SELECT *FROM canaries WHERE state='running'ORDER BY created DESC LIMIT 1").fetchone()
        if not row:return {'status':'no_active_canary'}
        runs=list(db.execute('SELECT r.*,f.owner FROM runs r JOIN families f ON f.id=r.family_id JOIN accounts a ON a.id=f.owner WHERE f.policy_id=?AND r.assignment_id IS NULL AND a.origin=\'human\'AND r.created>=?',(row['candidate'],row['created'])))
        done=[r for r in runs if r['state']in {'complete','failed'}];critical=any(json.loads(r['result']or'{}').get('critical_safety_or_provenance_error')for r in done);error_rate=sum(technical_failure(dict(r))for r in done)/max(1,len(done));state='running'
        if critical or(done and error_rate-row['baseline_error']>.02):state='rolled_back'
        elif len(done)>=20:state='promoted';db.execute('UPDATE meta SET value=?WHERE key=?',(row['candidate'],'active_policy'));db.execute("UPDATE policies SET status='promoted'WHERE id=?",(row['candidate'],))
        elif stamp()>=row['expires']:state='insufficient_traffic'
        db.execute('UPDATE canaries SET state=?,completed_runs=?,critical_failure=?WHERE id=?',(state,len(done),int(critical),row['id']))
        store.audit('canary_observation',detail={'id':row['id'],'state':state,'completed':len(done),'technical_error_rate':error_rate},db=db)
        return {'status':state,'completed_runs':len(done),'baseline_kept':state!='promoted','technical_error_rate':error_rate}

def revert_policy(store,actor=None):
    with cycle_lock(store),store.tx()as db:
        active=store.meta('active_policy',db=db);row=db.execute('SELECT parent_id FROM policies WHERE id=?',(active,)).fetchone();parent=row[0]if row else None
        db.execute("UPDATE canaries SET state='reverted'WHERE state='running'")
        if parent:db.execute('UPDATE meta SET value=?WHERE key=?',(parent,'active_policy'))
        store.audit('policy_reverted',actor,{'previous':active,'active':parent or active},db)
        return {'acknowledged':True,'active_policy':parent or active}
