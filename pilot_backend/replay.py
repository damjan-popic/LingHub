"""Bounded private real-gateway replay, separate from independent human grading."""
from __future__ import annotations
import copy,fcntl,hashlib,json,os,re,stat,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from .store import canonical,digest,stamp
from .cycles import private_write,validate_candidate,snapshot,suggestions
from .security import Failure

VERSION='pilot-replay-v1'
SCHEMA='klikai.pilot.exposed-replay.v1'
CASE_KEYS={'case_id','question','context','locale','task','tier','model','sources','source_groups','original_case_sha256','exposure_set'}

def file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read_private(path,limit=8*1024**2):
    path=Path(path);attrs=path.lstat()
    if not stat.S_ISREG(attrs.st_mode)or attrs.st_mode&0o027 or attrs.st_uid not in{0,os.geteuid()}or attrs.st_size>limit:raise ValueError('Protected bounded replay input required')
    return json.loads(path.read_text())

def manifest(path,expected):
    path=Path(path)
    if not re.fullmatch('[a-f0-9]{64}',expected)or file_sha(path)!=expected:raise ValueError('Replay case manifest hash differs')
    data=read_private(path,65536)
    if set(data)!={'schema','origin','cases'}or data['schema']!=SCHEMA or data['origin']!='previously_exposed_regression' or not isinstance(data['cases'],list)or not 1<=len(data['cases'])<=4:raise ValueError('Replay requires a bounded exposed-regression manifest')
    ids=set()
    for item in data['cases']:
        if set(item)!=CASE_KEYS or item['case_id']in ids or not re.fullmatch('[a-z0-9_-]{1,80}',item['case_id']):raise ValueError('Invalid replay case identity')
        ids.add(item['case_id'])
        if item['exposure_set']not in{'exposed_regression','exposed_regression_minimal_pair','development'}:raise ValueError('Reserved or unexposed cases cannot be replayed')
        if item['locale']not in{'sl','it'}or item['task']!='reference'or item['tier']not in{'short','long'}or item['model']not in{'qwen','gemma'}:raise ValueError('Unsupported replay case settings')
        if not isinstance(item['question'],str)or not 1<=len(item['question'].strip())<=5000 or not isinstance(item['context'],str)or len(item['context'])>3000:raise ValueError('Invalid frozen replay query')
        if not isinstance(item['sources'],list)or not item['sources']or len(item['sources'])!=len(set(item['sources']))or set(item['sources'])-{'jezikovna_svetovalnica','epravopis'}:raise ValueError('Invalid frozen source settings')
        if not isinstance(item['source_groups'],list)or not item['source_groups']or any(not isinstance(x,str)or len(x)>512 for x in item['source_groups'])or not re.fullmatch('[a-f0-9]{64}',item['original_case_sha256']):raise ValueError('Exposed source-group provenance is missing')
    return data

def save(path,data):
    path=Path(path)
    if path.is_symlink():raise ValueError('Unsafe replay state path')
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');private_write(temporary,canonical(data)+'\n');os.replace(temporary,path)

def busy(store):
    with store.connect()as db:return bool(db.execute("SELECT 1 FROM jobs WHERE state IN('queued','running') LIMIT 1").fetchone())

def technical(result):
    return (isinstance(result,dict)and result.get('status')=='validated'and result.get('answer_kind')in{'generated','source_only'}
            and isinstance(result.get('evidence'),list)and bool(result.get('short_answer')or result.get('answer_md'))
            and not result.get('critical_safety_or_provenance_error'))

def source_model_pin(frozen):
    value=frozen['bundle']['payload']
    profiles=copy.deepcopy(value['profiles'])
    for tier in profiles.values():
        for profile in tier.values():profile.pop('profile_id',None);profile.pop('policy',None)
    return {'snapshot_id':value['snapshot_id'],'collection_id':value['collection_id'],'base_policy_version':value['base_policy_version'],
            'models':value['models'],'profiles':profiles,'common_context_limit':value['common_context_limit']}

class Deferred(Exception):pass

def current_pin(gateway):
    if gateway is None:raise ValueError('A current private gateway observation is required')
    observed=gateway.request('/internal/pilot/identity',None);value=observed['source_model_profile_pin']
    if not isinstance(value,dict)or set(value)!={'snapshot_id','collection_id','base_policy_version','models','profiles','common_context_limit'}:raise ValueError('Current replay runtime pin is incomplete')
    retrieval=observed['retrieval_pin']
    if not isinstance(retrieval,dict)or set(retrieval)!={'embedding_contract_id','reranker_contract_id','embedding','reranker'}:raise ValueError('Current retrieval pin is incomplete')
    return {'source_model_profile_pin':value,'retrieval_pin':retrieval}

def run(store,gateway,cases_path,cases_sha,*,candidate=None,max_cases=4,force_test=False,period=None):
    if type(max_cases)is not int or not 1<=max_cases<=4:raise ValueError('Replay is bounded to at most four cases')
    cases=manifest(cases_path,cases_sha);cutoff,rows,_=snapshot(store)
    with store.connect()as db:
        base_id=store.meta('active_policy',db=db);base=json.loads(db.execute('SELECT config FROM policies WHERE id=?',(base_id,)).fetchone()[0])
        consumed=db.execute("SELECT coalesce(max(cutoff),0)FROM cycles WHERE kind='adaptation'AND status='CANARY_PENDING'").fetchone()[0]
    human=[r for r in rows if r['origin']=='human'and r['task']=='reference'and r['seq']>consumed]
    eligible=len(human)>=20 and len({r['owner']for r in human})>=3 and len({r['family_id']for r in human})>=10
    if not force_test and not eligible:return {'status':'INSUFFICIENT_DATA','model_requests':0,'baseline_kept':True,'human_events':len(human),'force_test':False}
    if store.meta('adaptation_paused','0')=='1'and not force_test:return {'status':'PAUSED','model_requests':0,'baseline_kept':True,'force_test':False}
    if busy(store):return {'status':'DEFERRED_PUBLIC_JOBS','model_requests':0,'baseline_kept':True,'force_test':bool(force_test)}
    try:expected_pin=current_pin(gateway)
    except Exception:return {'status':'CURRENT_IDENTITY_UNAVAILABLE','model_requests':0,'baseline_kept':True,'force_test':bool(force_test)}
    choices=suggestions(human,base)
    if candidate is None:
        candidate=choices[0]['config']if choices else dict(base,short_style_preset='direct_examples_v1'if base['short_style_preset']=='direct_qualified_v1'else'direct_qualified_v1')
    pid=validate_candidate(base,candidate)
    period=period or datetime.now(timezone.utc).strftime('%G-W%V')
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}',period):raise ValueError('Invalid replay period')
    identity={'version':VERSION,'implementation_sha256':file_sha(__file__),'manifest_sha256':cases_sha,'baseline_policy_id':base_id,'candidate_policy_id':pid,'period':period,'force_test':bool(force_test),'current_runtime_pin':expected_pin}
    root=store.path.parent/'replay';root.mkdir(mode=0o700,exist_ok=True)
    if root.is_symlink()or root.stat().st_mode&0o077:raise ValueError('Replay state directory must remain private')
    fd=os.open(root/'replay.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a')as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'DEFERRED_LOCK','model_requests':0,'baseline_kept':True,'force_test':bool(force_test)}
        run_id=digest(identity);leaf=root/run_id;leaf.mkdir(mode=0o700,exist_ok=True)
        if leaf.is_symlink()or leaf.stat().st_mode&0o077:raise ValueError('Replay leaf must remain private')
        path=leaf/'state.json'
        with store.connect()as db:retained=db.execute("SELECT report FROM cycles WHERE id=?AND kind='replay'",(run_id,)).fetchone()
        state=read_private(path)if path.exists()else json.loads(retained[0])if retained else{'identity':identity,'run_id':run_id,'created':stamp(),'requests':{},'cases':{},'status':'RUNNING','cursor':0}
        def persist():
            save(path,state)
            # Hashes, pins and cursor survive the normal protected SQLite backup.
            # Missing external result files after restore always fail closed.
            with store.tx()as db:db.execute('''INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id)DO UPDATE SET status=excluded.status,report=excluded.report,finished=excluded.finished''',
                    (run_id,'replay',cutoff,consumed,digest(identity),state['status'],canonical(state),state['created'],stamp()))
        if state['identity']!=identity:raise ValueError('Replay identity differs')
        if state['status']=='COMPLETE':
            try:
                for name,item in state['requests'].items():
                    if item['status']!='PASS'or file_sha(leaf/(name+'.json'))!=item['sha256']:raise ValueError('Completed replay artifact changed')
            except (OSError,ValueError):return {'status':'RECOVERY_ARTIFACTS_UNAVAILABLE','model_requests':0,'run_id':run_id,'baseline_kept':True,'force_test':bool(force_test)}
            if not path.exists():save(path,state)
            save(root/('latest-test.json'if force_test else'latest.json'),{'report':str(path),'sha256':file_sha(path),'run_id':run_id})
            return {'status':'COMPLETE','run_id':run_id,'report':str(path),'sha256':file_sha(path),'model_requests':0,'cases_complete':len(state['cases']),'case_count':len(cases['cases']),
                    'model_generated_outputs':state['model_generated_outputs'],'all_technical_checks_pass':state['all_technical_checks_pass'],'force_test':bool(force_test),'baseline_kept':True,'human_quality_scores_created':False}
        calls=0;completed=0
        def request(name,route,payload):
            nonlocal calls
            request_hash=digest({'route':route,'payload':payload});entry=state['requests'].setdefault(name,{'request_hash':request_hash});output=leaf/(name+'.json')
            if entry['request_hash']!=request_hash:raise ValueError('Frozen replay request changed')
            if output.exists():
                saved=read_private(output)
                if saved['request_hash']!=request_hash or entry.get('sha256',file_sha(output))!=file_sha(output):raise ValueError('Replay response artifact changed')
                entry.update(status='PASS',sha256=file_sha(output));persist();return saved['response']
            if entry.get('status')in{'RUNNING','INTERRUPTED','FAILED','PASS'}:
                entry['status']='INTERRUPTED';persist();raise Failure('replay_interrupted_or_artifact_missing',409)
            if busy(store):raise Deferred()
            entry.update(status='RUNNING',started=stamp());persist();start=time.monotonic();calls+=int(route.endswith('/generate'))
            try:response=gateway.request(route,payload)
            except Exception:
                entry.update(status='FAILED',finished=stamp());persist();raise
            save(output,{'request_hash':request_hash,'response':response,'seconds':time.monotonic()-start})
            entry.update(status='PASS',sha256=file_sha(output),finished=stamp());persist();return response
        try:
            for index,case in enumerate(cases['cases']):
                if case['case_id']in state['cases']:continue
                if completed>=max_cases:break
                pair={}
                for label,policy in(('baseline',base),('candidate',candidate)):
                    prefix=str(index)+'-'+label
                    frozen=request(prefix+'-freeze','/internal/pilot/freeze',{**{key:case[key]for key in('question','context','locale','task','sources')},'policy':policy})
                    pin=source_model_pin(frozen)
                    if pin!=expected_pin['source_model_profile_pin']:raise ValueError('Source/model/profile identity changed during paired replay')
                    state['source_model_profile_pin']=pin;persist()
                    short=request(prefix+'-short','/internal/pilot/generate',{'bundle':frozen['bundle'],'tier':'short','model':case['model'],'parent_answer':None})
                    result=short if case['tier']=='short'else request(prefix+'-long','/internal/pilot/generate',{'bundle':frozen['bundle'],'tier':'long','model':case['model'],'parent_answer':short})
                    pair[label]={'technical_pass':technical(short)and technical(result),'status':result.get('status'),'answer_kind':result.get('answer_kind'),'profile_pin':digest(frozen['identity']['profiles']),
                                 'evidence_packet_hash':frozen['bundle']['payload']['packets'][case['tier']]['evidence_packet_hash'],'result_sha256':digest(result)}
                if current_pin(gateway)!=expected_pin:raise ValueError('Runtime or retrieval changed during paired replay')
                state['cases'][case['case_id']]={'source_groups':case['source_groups'],'locale':case['locale'],'tier':case['tier'],'model':case['model'],'pair':pair}
                state['cursor']=index+1;completed+=1;persist()
            state['status']='COMPLETE'if len(state['cases'])==len(cases['cases'])else'PARTIAL'
        except Deferred:state['status']='DEFERRED_PUBLIC_JOBS'
        except Exception as error:state.update(status='FAILED',error_code=error.code if isinstance(error,Failure)else'integrity_or_runtime_failure')
        state.update(updated=stamp(),all_technical_checks_pass=state['status']=='COMPLETE'and all(x['technical_pass']for c in state['cases'].values()for x in c['pair'].values()),human_quality_scores_created=False,baseline_kept=True)
        state['model_generated_outputs']=sum(x['answer_kind']=='generated'for c in state['cases'].values()for x in c['pair'].values());persist()
        # This index points only to a fully hashed private report; raw responses stay private.
        if state['status']=='COMPLETE':save(root/('latest-test.json'if force_test else'latest.json'),{'report':str(path),'sha256':file_sha(path),'run_id':run_id})
        return {'status':state['status'],'run_id':run_id,'report':str(path),'sha256':file_sha(path),'model_requests':calls,'cases_complete':len(state['cases']),'case_count':len(cases['cases']),
                'model_generated_outputs':state['model_generated_outputs'],'all_technical_checks_pass':state['all_technical_checks_pass'],'force_test':bool(force_test),'baseline_kept':True,'human_quality_scores_created':False}

def gate(store,base,candidate,*,gateway=None,cases_path=None,cases_sha=None):
    root=store.path.parent/'replay';index=root/'latest.json'
    if not index.exists():return False,'actual_replay_missing',{}
    try:
        if cases_path is None or cases_sha is None:return False,'current_replay_manifest_required',{}
        required_cases=manifest(cases_path,cases_sha)
        saved=read_private(index);path=Path(saved['report'])
        if path.parent.parent!=root or path.name!='state.json'or file_sha(path)!=saved['sha256']:raise ValueError('Replay receipt differs')
        report=read_private(path);identity=report['identity']
        if identity['manifest_sha256']!=cases_sha or set(report['cases'])!={c['case_id']for c in required_cases['cases']}:return False,'current_replay_manifest_mismatch',{}
        for case in required_cases['cases']:
            if any(report['cases'][case['case_id']][key]!=case[key]for key in('source_groups','locale','tier','model')):return False,'current_replay_case_settings_mismatch',{}
        if current_pin(gateway)!=identity['current_runtime_pin']or report['source_model_profile_pin']!=identity['current_runtime_pin']['source_model_profile_pin']:return False,'current_replay_runtime_pin_mismatch',{}
        if report['status']!='COMPLETE'or not report['all_technical_checks_pass']or not report['model_generated_outputs']:return False,'actual_replay_failed',{}
        if identity['force_test']:return False,'test_replay_is_not_promotion_evidence',{}
        if identity['baseline_policy_id']!=base or identity['candidate_policy_id']!=candidate or identity['implementation_sha256']!=file_sha(__file__)or not 0<=stamp()-report['updated']<=8*86400:return False,'replay_pin_or_age_mismatch',{}
        for name,item in report['requests'].items():
            if item['status']!='PASS'or file_sha(path.parent/(name+'.json'))!=item['sha256']:raise ValueError('Replay result differs')
        return True,'actual_replay_passed',{'run_id':report['run_id'],'current_runtime_pin':identity['current_runtime_pin'],'source_model_profile_pin':report['source_model_profile_pin'],'source_groups':sorted({g for c in report['cases'].values()for g in c['source_groups']})}
    except (ValueError,OSError,KeyError,TypeError,Failure):return False,'actual_replay_receipt_invalid',{}
