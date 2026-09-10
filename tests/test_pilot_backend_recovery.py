import hashlib,hmac,json,secrets,sqlite3
from pathlib import Path
import pytest
from pilot_backend.store import Store,canonical,digest
from pilot_backend.security import Auth
from pilot_backend.backup import backup,verify_backup,sha
from pilot_backend.recovery import build_support,restore_support,sanitized_config
from argon2 import PasswordHasher

@pytest.fixture
def fixture(tmp_path):
    root=tmp_path/'original';store=Store(root/'pilot.sqlite');auth=Auth(store,PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1));owner=auth.create('test-user','synthetic password only',origin='synthetic',must_change=False);auth.issue(owner)
    key=root/'signing-key';key.write_bytes(secrets.token_bytes(64));key.chmod(0o600)
    configs={}
    for kind,value in {'backend':{'store':str(store.path),'gateway_token_file':'/private/runtime-token'},'gateway':{'source_release':'/fixed/source','pilot':{'signing_key_file':str(key),'internal_token_file':'/private/runtime-token','tokenizer_python':'/dedicated/python'}},'pins':{'git_commits':{'klikai':'a'*40,'linghub':'b'*40},'source_snapshot_id':'c'*64,'collection_id':'d'*64,'policy_version':'e'*64}}.items():
        path=root/(kind+'.json');path.write_text(canonical(value));path.chmod(0o600);configs[kind]=path
    archive=tmp_path/'feedback.tar.gz';receipt=backup(store,archive)
    return store,owner,key,configs,archive,receipt

def package(fixture,tmp_path):
    store,owner,key,cfg,archive,_=fixture
    return build_support(archive,store.deletion_path,key,cfg['backend'],cfg['gateway'],cfg['pins'],tmp_path/'support.tar.gz')

def test_support_actual_restore_key_and_signed_bundle(fixture,tmp_path):
    store,owner,key,cfg,archive,_=fixture
    # The gateway's exact canonical HMAC bundle format. No model or source runtime is started.
    payload={'family_basis_hash':'f'*64,'question':'synthetic fixture','policy_version':'e'*64};bundle={'payload':payload,'payload_hash':digest(payload)};bundle['signature']=hmac.new(key.read_bytes(),canonical(bundle).encode(),hashlib.sha256).hexdigest()
    # Account deletion AFTER the feedback backup must travel with the support package.
    store.delete_account(owner);support=package(fixture,tmp_path);dest=tmp_path/'restored';proof=restore_support(support['archive'],dest,support['sha256'])
    restored_key=(dest/'evidence-signing-key').read_bytes();body={k:bundle[k]for k in('payload','payload_hash')};assert hmac.compare_digest(bundle['signature'],hmac.new(restored_key,canonical(body).encode(),hashlib.sha256).hexdigest());assert digest(bundle['payload'])==bundle['payload_hash']
    assert restored_key==key.read_bytes();assert proof['feedback_restore']['counts']['accounts']==0;assert not proof['fixed_runtime_started']and not proof['active_pointers_changed'];assert not proof['offhost_verified']
    for member in dest.rglob('*'):
        if member.is_file():assert member.stat().st_mode&0o077==0
    with pytest.raises(ValueError):restore_support(support['archive'],dest,support['sha256'])

def test_feedback_backup_excludes_session_runtime_rows(fixture,tmp_path):
    import tarfile
    store,owner,key,cfg,archive,_=fixture
    with store.connect()as db:
        runtime_session=db.execute('SELECT token_hash,csrf FROM sessions').fetchone();assert runtime_session
    with tarfile.open(archive,'r:gz')as tar:sqlite=tar.extractfile('pilot.sqlite').read()
    db=sqlite3.connect(':memory:');db.deserialize(sqlite);assert db.execute('SELECT count(*)FROM sessions').fetchone()[0]==0;db.close()
    assert all(value.encode()not in sqlite for value in runtime_session)

@pytest.mark.parametrize('value',[{'password':'never'},{'gateway_token':'never'},{'DATABASE_URL':'postgresql://user:private@host/db'},{'raw_text':'body'},{'question':'privatequestion'},{'credentials_file':'/tmp/key'}])
def test_support_metadata_rejects_secrets_and_bodies(value):
    with pytest.raises(ValueError):sanitized_config(value)

def test_key_permissions_and_incompatible_ledger_rejected(fixture,tmp_path):
    store,owner,key,cfg,archive,_=fixture;key.chmod(0o644)
    with pytest.raises(ValueError):package(fixture,tmp_path)
    key.chmod(0o600);store.deletion_path.write_text('{"broken":true}\n')
    with pytest.raises((ValueError,KeyError)):package(fixture,tmp_path)

@pytest.mark.parametrize('change',['unexpected_path','symlink','member_bytes'])
def test_support_archive_inventory_and_hashes_fail_closed(fixture,tmp_path,change):
    import io,tarfile
    support=package(fixture,tmp_path);altered=tmp_path/'altered.tar.gz'
    with tarfile.open(support['archive'],'r:gz')as source,tarfile.open(altered,'w:gz')as target:
        for info in source.getmembers():
            data=source.extractfile(info).read()
            if info.name=='pins.json':
                if change=='unexpected_path':info.name='../outside.json'
                elif change=='symlink':info.type=tarfile.SYMTYPE;info.linkname='../outside.json';info.size=0;data=b''
                else:data=b'x'+data[1:]
            target.addfile(info,io.BytesIO(data))
    altered.chmod(0o600)
    with pytest.raises(ValueError):restore_support(altered,tmp_path/'bad-restore',sha(altered))
    assert not(tmp_path/'outside.json').exists()

def test_support_restore_refuses_unprotected_archive(fixture,tmp_path):
    support=package(fixture,tmp_path);Path(support['archive']).chmod(0o644)
    with pytest.raises(ValueError):restore_support(support['archive'],tmp_path/'bad-restore',support['sha256'])
    assert not(tmp_path/'bad-restore').exists()
