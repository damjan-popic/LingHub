import json,sqlite3
import pytest
from pilot_backend.store import Store,canonical,digest,stamp,uid,BASE_POLICY
from pilot_backend.security import Auth,Failure
from pilot_backend.domain import Domain
from pilot_backend.jobs import Jobs
from pilot_backend.cycles import analyse,adapt,validate_candidate,independent_gate,monitor_canary,revert_policy,snapshot,environment_pin
from pilot_backend.backup import exercise,restore,backup
from test_pilot_backend import Adapter,PASSWORD
from argon2 import PasswordHasher

@pytest.fixture
def records(tmp_path):
    store=Store(tmp_path/'private'/'pilot.sqlite');auth=Auth(store,PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1));domain=Domain(store);jobs=Jobs(store,Adapter());owners=[]
    for n in range(3):owners.append(auth.create('person-'+str(n),PASSWORD,origin='human',must_change=False))
    synthetic=auth.create('synthetic',PASSWORD,origin='synthetic',must_change=False)
    for i in range(21):
        who=owners[i%3]if i<20 else synthetic
        item=domain.create_family(who,{'question':'Question '+str(i),'idempotency_key':'family'+str(i).zfill(5)})
        while job:=jobs.claim():jobs.execute(job)
        f=domain.family(who,item['family']['answer_family_id']);run=f['short'];domain.delivered(who,run['run_id'],{'display_sha256':run['display_sha256']});domain.feedback(who,{'run_id':run['run_id'],'rating':'partly_useful','tags':['missing_context'],'idempotency_key':'feedback'+str(i).zfill(5)})
    return store,domain,owners

def validation(base,pid,store=None):
    return {'replay_run_id':'synthetic-machine-fixture','source_model_profile_cohort_pin':environment_pin([r for r in snapshot(store)[1]if r['origin']=='human'])if store else None,'source_reviewed':True,'independent_of_development':True,'deterministic_gate_passed':True,'regression_replay_passed':True,'development_replay_passed':True,'reviewer_kind':'independent_source_review','reviewer_id':'test-only-external-reviewer','baseline_policy_id':base,'candidate_policy_id':pid,'pairs':[{'case_id':'holdout-'+str(i),'source_group':'group-'+str(i),'origin':'human_reviewed_holdout','source_review_hash':digest(['fixture',i]),'baseline_score':.7,'candidate_score':.75,'baseline_seconds':10,'candidate_seconds':10,'critical_regression':False}for i in range(30)]}

@pytest.fixture
def machine_replay(monkeypatch):
    # Isolated canary-state tests explicitly inject a machine prerequisite;
    # ordinary validation-only fixtures below must now fail closed.
    from pilot_backend import replay
    from types import SimpleNamespace
    pin={'source_model_profile_pin':{'snapshot_id':'synthetic-source','collection_id':'synthetic-vector','base_policy_version':'synthetic-writer','models':{},'profiles':{},'common_context_limit':32768},'retrieval_pin':{'embedding_contract_id':'synthetic-embed','reranker_contract_id':'synthetic-rerank','embedding':{},'reranker':{}}}
    monkeypatch.setattr(replay,'gate',lambda *_,**__:(True,'actual_replay_passed',{'run_id':'synthetic-machine-fixture','source_groups':['synthetic-exposed-regression'],'current_runtime_pin':pin}))
    return SimpleNamespace(pin=pin,gateway=SimpleNamespace(request=lambda *_:pin))

def seed_canary_runs(store,owner,pid,pin,params,count=20,finished_offset=0):
    expected=pin['source_model_profile_pin'];identity={'source_snapshot_ids':[expected['snapshot_id']],'vector_snapshot_id':expected['collection_id'],'policy_version':digest({'base':expected['base_policy_version'],'parameters':params}),'models':expected['models'],'profiles':expected['profiles']}
    with store.tx()as db:
        for i in range(count):
            fid=uid();rid=uid();db.execute('INSERT INTO families VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,owner,'Fixture','','reference','sl','sl','sl','qwen',pid,'{}',canonical(identity),stamp()))
            db.execute('INSERT INTO runs(id,family_id,tier,model,revision,state,result,created,finished)VALUES(?,?,?,?,?,?,?,?,?)',(rid,fid,'short','qwen',1,'complete','{}',stamp(),stamp()+finished_offset))

def test_analysis_counts_idempotent_bilingual_private(records,tmp_path):
    store,domain,owners=records;r=analyse(store,tmp_path/'reports');assert r['counts']['human_feedback']==20;assert r['counts']['feedback_by_origin']['synthetic']==1;assert r['counts']['human_cases']==20
    assert analyse(store,tmp_path/'reports')==r
    assert set(r['reports'])=={'sl','it'};assert not r['comments_sent_to_model']
    for p in(tmp_path/'reports'/r['cycle_id']).iterdir():assert p.stat().st_mode&0o077==0

def test_revision_latest_not_duplicate(records):
    store,domain,owners=records
    f=domain.families(owners[0])['families'][0];rid=f['short']['run_id'];domain.feedback(owners[0],{'run_id':rid,'rating':'useful','tags':[],'idempotency_key':'changed-feedback'})
    result=analyse(store);assert result['counts']['human_feedback']==20

def test_no_validation_no_promotion(records):
    store,_,_=records;base=store.meta('active_policy');candidate=dict(BASE_POLICY,context_passage_limit=12)
    r=adapt(store,[candidate]);assert r['status']=='ANALYSIS_ONLY_NO_VALIDATED_PROMOTION';assert store.meta('active_policy')==base;assert r['candidates'][0]['reason']=='independent_validation_missing'

def test_canary_pending_then_promotion_and_revert(records,machine_replay):
    store,domain,owners=records;base=store.meta('active_policy');candidate=dict(BASE_POLICY,context_passage_limit=12);pid=digest(candidate)
    r=adapt(store,[candidate],{pid:validation(base,pid,store)});assert r['status']=='CANARY_PENDING';assert store.meta('active_policy')==base
    assert adapt(store,[candidate],{pid:validation(base,pid,store)})==r
    assert monitor_canary(store)['status']=='running'
    # Engineering gate fixture: twenty completed eligible new human runs, no comparison data counted.
    seed_canary_runs(store,owners[0],pid,machine_replay.pin,candidate)
    assert monitor_canary(store)['reason']=='current_runtime_observation_unavailable';assert store.meta('active_policy')==base
    assert monitor_canary(store,gateway=machine_replay.gateway)['status']=='promoted';assert store.meta('active_policy')==pid
    assert revert_policy(store)['active_policy']==base

def test_canary_immediate_critical_and_insufficient_traffic(records,machine_replay):
    store,domain,owners=records;base=store.meta('active_policy');candidate=dict(BASE_POLICY,context_passage_limit=12);pid=digest(candidate);adapt(store,[candidate],{pid:validation(base,pid,store)})
    with store.tx()as db:
        fid=uid();db.execute('INSERT INTO families VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,owners[0],'Fixture','','reference','sl','sl','sl','qwen',pid,'{}','{}',stamp()));db.execute('INSERT INTO runs(id,family_id,tier,model,revision,state,result,created)VALUES(?,?,?,?,?,?,?,?)',(uid(),fid,'short','qwen',1,'complete',canonical({'critical_safety_or_provenance_error':True}),stamp()))
    assert monitor_canary(store)['status']=='rolled_back';assert store.meta('active_policy')==base

def test_canary_deadline_keeps_baseline(records,machine_replay):
    store,_,_=records;base=store.meta('active_policy');candidate=dict(BASE_POLICY,context_passage_limit=12);pid=digest(candidate);adapt(store,[candidate],{pid:validation(base,pid,store)})
    with store.tx()as db:db.execute('UPDATE canaries SET expires=?',(stamp()-1,))
    assert monitor_canary(store)['status']=='insufficient_traffic';assert store.meta('active_policy')==base

def test_canary_current_retrieval_drift_rolls_back_instead_of_promoting(records,machine_replay):
    import copy
    from types import SimpleNamespace
    store,_,owners=records;base=store.meta('active_policy');params=dict(BASE_POLICY,context_passage_limit=12);pid=digest(params)
    adapt(store,[params],{pid:validation(base,pid,store)});seed_canary_runs(store,owners[0],pid,machine_replay.pin,params)
    changed=copy.deepcopy(machine_replay.pin);changed['retrieval_pin']['embedding_contract_id']='changed-active-contract'
    result=monitor_canary(store,gateway=SimpleNamespace(request=lambda *_:changed))
    assert result['status']=='rolled_back'and result['reason']=='current_runtime_drift'and store.meta('active_policy')==base

def test_canary_family_model_drift_rolls_back_before_any_health_probe(records,machine_replay):
    import copy
    from types import SimpleNamespace
    from unittest.mock import Mock
    store,_,owners=records;base=store.meta('active_policy');params=dict(BASE_POLICY,context_passage_limit=12);pid=digest(params)
    adapt(store,[params],{pid:validation(base,pid,store)});changed=copy.deepcopy(machine_replay.pin);changed['source_model_profile_pin']['base_policy_version']='new-runtime'
    seed_canary_runs(store,owners[0],pid,changed,params,count=1);request=Mock(side_effect=AssertionError('Drift must reject before network'))
    result=monitor_canary(store,gateway=SimpleNamespace(request=request))
    assert result['status']=='rolled_back'and result['reason']=='candidate_family_runtime_drift';request.assert_not_called()

def test_canary_late_completions_cannot_satisfy_48hour_window(records,machine_replay,monkeypatch):
    from pilot_backend import cycles
    store,_,owners=records;base=store.meta('active_policy');params=dict(BASE_POLICY,context_passage_limit=12);pid=digest(params)
    adapt(store,[params],{pid:validation(base,pid,store)});seed_canary_runs(store,owners[0],pid,machine_replay.pin,params,finished_offset=49*3600)
    now=stamp();monkeypatch.setattr(cycles,'stamp',lambda:now+50*3600)
    result=monitor_canary(store,gateway=machine_replay.gateway)
    assert result['status']=='insufficient_traffic'and result['completed_runs']==0 and store.meta('active_policy')==base

@pytest.mark.parametrize('task',['source_search','text_check','terminology'])
def test_legacy_non_reference_candidate_runs_neither_promote_nor_trigger_drift(records,machine_replay,task):
    store,_,owners=records;base=store.meta('active_policy');params=dict(BASE_POLICY,context_passage_limit=12);pid=digest(params)
    adapt(store,[params],{pid:validation(base,pid,store)})
    with store.tx()as db:
        for _ in range(20):
            fid=uid();db.execute('INSERT INTO families VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,owners[0],'CPU fixture','',task,'sl','sl','sl','qwen',pid,'{}','{}',stamp()))
            db.execute('INSERT INTO runs(id,family_id,tier,model,revision,state,result,created,finished)VALUES(?,?,?,?,?,?,?,?,?)',(uid(),fid,'short','qwen',1,'complete','{}',stamp(),stamp()))
    result=monitor_canary(store,gateway=machine_replay.gateway)
    assert result['status']=='running'and result['completed_runs']==0 and store.meta('active_policy')==base

def test_reference_error_baseline_excludes_failed_cpu_tasks(records,machine_replay):
    store,_,owners=records;base=store.meta('active_policy');params=dict(BASE_POLICY,context_passage_limit=12);pid=digest(params)
    with store.tx()as db:
        fid=uid();db.execute('INSERT INTO families VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,owners[0],'CPU fixture','','text_check','sl','sl','sl','qwen',base,'{}','{}',stamp()))
        db.execute('INSERT INTO runs(id,family_id,tier,model,revision,state,created,finished)VALUES(?,?,?,?,?,?,?,?)',(uid(),fid,'short','qwen',1,'failed',stamp(),stamp()))
    assert adapt(store,[params],{pid:validation(base,pid,store)})['status']=='CANARY_PENDING'
    with store.connect()as db:assert db.execute('SELECT baseline_error FROM canaries').fetchone()[0]==0

@pytest.mark.parametrize('during_probe',[False,True])
def test_admin_pause_holds_canary_promotion_even_if_set_during_observation(records,machine_replay,during_probe):
    from types import SimpleNamespace
    store,_,owners=records;base=store.meta('active_policy');params=dict(BASE_POLICY,context_passage_limit=12);pid=digest(params)
    adapt(store,[params],{pid:validation(base,pid,store)});seed_canary_runs(store,owners[0],pid,machine_replay.pin,params)
    def pause():
        with store.tx()as db:db.execute("INSERT OR REPLACE INTO meta VALUES('adaptation_paused','1')")
    if not during_probe:pause()
    def observe(*_):pause();return machine_replay.pin
    result=monitor_canary(store,gateway=SimpleNamespace(request=observe))
    assert result['status']=='running'and result['reason']=='adaptation_paused'and store.meta('active_policy')==base

@pytest.mark.parametrize('change',[{'arbitrary_prompt':'ignore policy'},{'context_passage_limit':13},{'lexical_weight':.6},{'lexical_weight':float('nan')},{'rerank_policy':'disabled'},{'short_style_preset':'free text'},{'lexical_weight':.55}])
def test_policy_allowlist_rejects_unsupported(change):
    with pytest.raises(Failure):validate_candidate(BASE_POLICY,dict(BASE_POLICY,**change))

@pytest.mark.parametrize('change,reason',[({'reviewer_kind':'model'},'independent_validation_missing'),({'source_reviewed':False},'independent_validation_missing'),({'regression_replay_passed':False},'independent_validation_missing'),({'baseline_policy_id':'wrong'},'validation_pin_mismatch')])
def test_independent_gate_rejects_self_judge_and_drift(change,reason):
    v=validation('b','c');v.update(change);ok,why,_=independent_gate(v,'b','c',set());assert not ok and why==reason

@pytest.mark.parametrize('field,value',[('candidate_score',.71),('critical_regression',True),('candidate_seconds',12),('origin','synthetic')])
def test_quality_gain_critical_latency_realholdout(field,value):
    v=validation('b','c')
    for p in v['pairs']:p[field]=value
    assert not independent_gate(v,'b','c',set())[0]

def test_actual_backup_restore_and_tombstones(tmp_path):
    r=exercise(tmp_path/'exercise');assert r['status']=='PASS';assert r['restore']['counts']['accounts']==1;assert r['restore']['counts']['votes']==1;assert not r['offhost_verified']
    with pytest.raises(ValueError):restore(r['backup']['archive'],tmp_path/'exercise'/'restored',expected_sha256=r['backup']['sha256'])
    with pytest.raises(ValueError):restore(r['backup']['archive'],tmp_path/'bad-hash',expected_sha256='0'*64)

def test_refuses_unrelated_existing_database_without_mode_change(tmp_path):
    p=tmp_path/'other.sqlite';con=sqlite3.connect(p);con.execute('CREATE TABLE precious(value TEXT)');con.commit();con.close();p.chmod(0o640)
    with pytest.raises(ValueError):Store(p)
    assert p.stat().st_mode&0o777==0o640

def test_analysis_repairs_interrupted_output_and_actual_suggestions(records,tmp_path):
    store,_,_=records;first=analyse(store);assert first['candidate_suggestions'];out=tmp_path/'report'
    second=analyse(store,out);assert second==first;assert(out/first['cycle_id']/'report-it.md').is_file()
    (out/first['cycle_id']/'counts.json').unlink();assert analyse(store,out)==first;assert(out/first['cycle_id']/'counts.json').is_file()

def test_automatic_candidate_still_requires_independent_review(records):
    store,_,_=records;r=adapt(store);assert r['candidates'];assert r['status']=='ANALYSIS_ONLY_NO_VALIDATED_PROMOTION'
    assert r['candidates'][0]['reason']=='independent_validation_missing'

def test_independent_gate_pins_environment_and_separates_source_groups():
    v=validation('b','c');assert independent_gate(v,'b','c',set(),'realpin')[1]=='environment_pin_mismatch'
    v['source_model_profile_cohort_pin']='realpin';assert independent_gate(v,'b','c',set(),'realpin',{'group-0'})[1]=='holdout_overlap_or_unreviewed'

def test_retention_after_backup_replays_without_resurrection(records,tmp_path):
    store,domain,owners=records;archive=tmp_path/'backup.tar.gz';receipt=backup(store,archive)
    future=stamp()+181*86400;r=store.retention(now=future);assert r['days']==180
    with store.connect()as db:assert db.execute('SELECT count(*)FROM feedback').fetchone()[0]==0;assert db.execute('SELECT count(*)FROM families').fetchone()[0]==0
    proof=restore(archive,tmp_path/'restore-retained',store.deletion_path,receipt['sha256']);assert proof['counts']['feedback']==0;assert proof['counts']['families']==0;assert proof['counts']['accounts']==4

def test_initialization_failure_rolls_back_own_schema(tmp_path,monkeypatch):
    from pilot_backend import store as module
    schema=module.SCHEMA;monkeypatch.setattr(module,'SCHEMA',schema+'\nTHIS IS INVALID SQL;')
    with pytest.raises(sqlite3.OperationalError):Store(tmp_path/'private'/'pilot.sqlite')
    monkeypatch.setattr(module,'SCHEMA',schema);store=Store(tmp_path/'private'/'pilot.sqlite');assert store.meta('schema')=='pilot-v1'

def test_insufficient_data_explicit(tmp_path):
    store=Store(tmp_path/'private'/'pilot.sqlite');r=adapt(store);assert r['status']=='INSUFFICIENT_DATA';assert r['promotion_status']=='ANALYSIS_ONLY_NO_VALIDATED_PROMOTION'

def test_canary_counts_failed_provider_result_even_completed_job(records,machine_replay):
    store,_,owners=records;base=store.meta('active_policy');candidate=dict(BASE_POLICY,context_passage_limit=12);pid=digest(candidate);adapt(store,[candidate],{pid:validation(base,pid,store)})
    with store.tx()as db:
        fid=uid();db.execute('INSERT INTO families VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,owners[0],'Fixture','','reference','sl','sl','sl','qwen',pid,'{}','{}',stamp()));db.execute('INSERT INTO runs(id,family_id,tier,model,revision,state,result,created)VALUES(?,?,?,?,?,?,?,?)',(uid(),fid,'short','qwen',1,'complete',canonical({'status':'failed','answer_kind':'source_only'}),stamp()))
    assert monitor_canary(store)['status']=='rolled_back';assert store.meta('active_policy')==base

def test_verify_backup_is_read_only_complete_receipt(records,tmp_path):
    from pilot_backend.backup import verify_backup
    store,_,_=records;path=tmp_path/'verified.tar.gz';expected=backup(store,path);before={p.name:(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_mode)for p in tmp_path.iterdir()}
    actual=verify_backup(path);after={p.name:(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_mode)for p in tmp_path.iterdir()};assert actual==expected;assert before==after

@pytest.mark.parametrize('mutation',['extra','duplicate','link','schema','hash','length','truncated'])
def test_verify_backup_rejects_corrupt_member_inventory(records,tmp_path,mutation):
    import io,tarfile
    from pilot_backend.backup import verify_backup
    store,_,_=records;original=tmp_path/'original.tar.gz';backup(store,original)
    if mutation=='truncated':
        corrupt=tmp_path/'truncated.tar.gz';corrupt.write_bytes(original.read_bytes()[:50]);corrupt.chmod(0o600)
        with pytest.raises((ValueError,EOFError,tarfile.TarError)):verify_backup(corrupt)
        assert corrupt.exists();return
    with tarfile.open(original,'r:gz')as tar:contents={m.name:tar.extractfile(m).read()for m in tar.getmembers()}
    manifest=json.loads(contents['manifest.json'])
    if mutation=='schema':manifest['schema']='unexpected'
    if mutation=='hash':manifest['members']['pilot.sqlite']['sha256']='0'*64
    if mutation=='length':manifest['members']['pilot.sqlite']['bytes']+=1
    contents['manifest.json']=canonical(manifest).encode();corrupt=tmp_path/'corrupt.tar.gz'
    with tarfile.open(corrupt,'w:gz')as tar:
        for name,data in contents.items():
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600;tar.addfile(info,io.BytesIO(data))
        if mutation in{'extra','duplicate','link'}:
            info=tarfile.TarInfo('unexpected'if mutation!='duplicate'else'manifest.json');info.size=0
            if mutation=='link':info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd'
            tar.addfile(info,io.BytesIO())
    corrupt.chmod(0o600)
    with pytest.raises(ValueError):verify_backup(corrupt)
    assert corrupt.exists()
