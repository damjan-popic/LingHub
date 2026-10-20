"""Private SQLite backup and disposable restore with monotone deletion replay."""
from __future__ import annotations
import hashlib,json,os,shutil,sqlite3,tarfile,tempfile
from pathlib import Path
from .store import Store,canonical,stamp
from .cycles import private_write

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb')as f:
        while chunk:=f.read(1024*1024):h.update(chunk)
    return h.hexdigest()

def private_directory(path):
    path=Path(path).absolute()
    if path.exists()or path.is_symlink():raise ValueError('Choose a new disposable private directory')
    path.mkdir(parents=True,mode=0o700)
    # Own new leaf only: inherited named ACL entries have no effective group mask.
    os.chmod(path,0o700)
    return path

def backup(store,archive):
    archive=Path(archive).absolute()
    if archive.exists()or archive.is_symlink():raise ValueError('Backup destination already exists')
    archive.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.pilot-backup-',dir=archive.parent)as temporary:
        root=Path(temporary);dbpath=root/'pilot.sqlite';fd=os.open(dbpath,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        # SQLite online backup is a consistent committed snapshot, including queued job identities.
        source=store.connect();target=sqlite3.connect(dbpath)
        try:
            source.backup(target);target.execute('PRAGMA foreign_keys=ON');target.execute('PRAGMA secure_delete=ON');target.execute('DELETE FROM sessions');target.commit()
            # Only the disposable snapshot is normalized to a standalone database.
            # Never retain session runtime material or require a companion WAL file.
            target.execute('PRAGMA journal_mode=DELETE')
            target.execute('VACUUM')
        finally:source.close();target.close()
        # Append-first deletion journal is authoritative; it may include deletions newer than the DB snapshot.
        ledger=store.deletion_records();private_write(root/'pilot.deletions.jsonl',''.join(canonical(r)+'\n'for r in ledger))
        manifest={'schema':'klikai.pilot.private-backup.v1','created':stamp(),'members':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size}for p in(root/'pilot.sqlite',root/'pilot.deletions.jsonl')},'offhost_verified':False}
        private_write(root/'manifest.json',canonical(manifest)+'\n')
        fd=os.open(archive,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb')as out,tarfile.open(fileobj=out,mode='w:gz')as tar:
            for name in ('manifest.json','pilot.sqlite','pilot.deletions.jsonl'):
                info=tar.gettarinfo(str(root/name),arcname=name);info.uid=info.gid=0;info.uname=info.gname='';info.mode=0o600
                with(root/name).open('rb')as source:tar.addfile(info,source)
        os.chmod(archive,0o600)
    return {'archive':str(archive),'sha256':sha(archive),'bytes':archive.stat().st_size,'mode':'0600','offhost_verified':False,'manifest':manifest}

def restore(archive,destination,current_deletions=None,expected_sha256=None):
    archive=Path(archive)
    if expected_sha256 and sha(archive)!=expected_sha256:raise ValueError('Backup archive hash differs')
    root=private_directory(destination)
    with tarfile.open(archive,'r:gz')as tar:
        members=tar.getmembers()
        if {m.name for m in members}!={'manifest.json','pilot.sqlite','pilot.deletions.jsonl'}or len(members)!=3 or any(not m.isfile()or m.size>2*1024**3 for m in members):raise ValueError('Unexpected backup member')
        for m in members:
            stream=tar.extractfile(m)
            fd=os.open(root/m.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb')as out:
                shutil.copyfileobj(stream,out);out.flush();os.fsync(out.fileno())
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest.get('schema')!='klikai.pilot.private-backup.v1'or set(manifest.get('members',{}))!={'pilot.sqlite','pilot.deletions.jsonl'}:raise ValueError('Invalid backup manifest')
    for name,pin in manifest['members'].items():
        path=root/name
        if path.stat().st_size!=pin['bytes']or sha(path)!=pin['sha256']:raise ValueError('Backup member differs: '+name)
    # Refuse replacing/truncating a newer deletion chain with the backup's earlier chain.
    if current_deletions:
        path=Path(current_deletions);new=path.read_text()if path.exists()else'';old=(root/'pilot.deletions.jsonl').read_text()
        if old and not new.startswith(old):raise ValueError('Current deletion ledger does not extend backup ledger')
        (root/'pilot.deletions.jsonl').write_text(new)
    store=Store(root/'pilot.sqlite');store.recover_jobs()
    with store.tx()as db:
        db.execute('DELETE FROM sessions')
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0];foreign=list(db.execute('PRAGMA foreign_key_check'))
        counts={table:db.execute(f'SELECT count(*)FROM {table}').fetchone()[0]for table in ('accounts','families','runs','jobs','assignments','votes','feedback','feedback_events','cycles','policies','tombstones')}
    if integrity!='ok'or foreign:raise ValueError('Restored account database failed integrity checks')
    return {'destination':str(root),'sha256':sha(archive),'integrity':integrity,'foreign_key_violations':len(foreign),'counts':counts,'deletion_chain_length':len(store.deletion_records()),'sessions_revoked':True,'offhost_verified':False}

def exercise(directory):
    """Actual isolated synthetic account/feedback/assignment backup and tombstone proof."""
    from argon2 import PasswordHasher
    from .security import Auth
    from .domain import Domain
    from .jobs import Jobs
    from .cycles import analyse
    root=private_directory(directory);store=Store(root/'original'/'pilot.sqlite');auth=Auth(store,PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1));domain=Domain(store)
    class Synthetic:
        def freeze(self,f,policy):return {'bundle':{'question':f['question']},'identity':{'fixture':'synthetic'}}
        def generate(self,*args):return {'status':'validated','short_answer':'Samo preizkus 😀.','answer_md':'Samo preizkus 😀.','source_badges':[],'evidence':[]}
    owner=auth.create('synthetic-owner','only synthetic password',origin='synthetic',must_change=False);keep=auth.create('synthetic-keep','only synthetic password',origin='synthetic',must_change=False);jobs=Jobs(store,Synthetic())
    saved=[]
    for who in (owner,keep):
        item=domain.create_family(who,{'question':'Sintetičen preizkus?','idempotency_key':'syntheticfamily1'});saved.append(item)
        while job:=jobs.claim():jobs.execute(job)
        f=domain.family(who,item['family']['answer_family_id']);run=f['short'];domain.delivered(who,run['run_id'],{'display_sha256':run['display_sha256']})
        domain.feedback(who,{'run_id':run['run_id'],'rating':'useful','tags':[],'idempotency_key':'syntheticfeedback1'})
        a=domain.create_comparison(who,{'family_id':f['answer_family_id'],'idempotency_key':'syntheticcompare1','blind':True})
        while job:=jobs.claim():jobs.execute(job)
        domain.comparison_delivery(who,a['comparison_id'],{'tier':'short'});domain.vote(who,a['comparison_id'],{'tier':'short','choice':'equivalent','idempotency_key':'syntheticvote1'})
    analysis=analyse(store);inflight=domain.create_family(keep,{'question':'Prekinjeno opravilo','idempotency_key':'inflight00001'});claimed=jobs.claim()
    archive=root/'private-backup.tar.gz';back=backup(store,archive)
    # Delete AFTER backup, proving that replay prevents restoring the earlier user's records.
    store.delete_account(owner)
    proof=restore(archive,root/'restored',store.deletion_path,back['sha256']);restored=Store(root/'restored'/'pilot.sqlite')
    with restored.connect()as db:
        assert db.execute('SELECT count(*)FROM accounts WHERE id=?',(owner,)).fetchone()[0]==0
        assert db.execute('SELECT count(*)FROM families WHERE owner=?',(owner,)).fetchone()[0]==0
        assert db.execute('SELECT count(*)FROM feedback WHERE owner=?',(owner,)).fetchone()[0]==0
        assert db.execute('SELECT count(*)FROM assignments WHERE owner=?',(owner,)).fetchone()[0]==0
        assert db.execute('SELECT count(*)FROM accounts WHERE id=?',(keep,)).fetchone()[0]==1
        assert db.execute('SELECT state FROM jobs WHERE id=?',(claimed['id'],)).fetchone()[0]=='failed'
        assert db.execute('SELECT cutoff FROM cycles WHERE id=?',(analysis['cycle_id'],)).fetchone()[0]==analysis['cutoff']
        assert db.execute('SELECT count(*)FROM votes WHERE owner=?',(keep,)).fetchone()[0]==1
    receipt={'schema':'klikai.pilot.synthetic-recovery-proof.v1','status':'PASS','backup':back,'restore':proof,'checks':['consistent_committed_snapshot','account_and_owned_data_deletion_replayed','surviving_feedback_and_blind_assignment_vote','analysis_watermark_preserved','inflight_marked_interrupted_without_reexecution','foreign_keys','session_revocation'],'offhost_verified':False}
    private_write(root/'receipt.json',canonical(receipt)+'\n');return receipt


def verify_backup(archive):
    """Verify a completed private archive in place, without extraction or mutation."""
    import math,re,stat
    archive=Path(archive).absolute();attrs=archive.lstat()
    if not stat.S_ISREG(attrs.st_mode)or stat.S_IMODE(attrs.st_mode)!=0o600:raise ValueError('Backup must be a private0600 regular file')
    names={'manifest.json','pilot.sqlite','pilot.deletions.jsonl'}
    with tarfile.open(archive,'r:gz')as tar:
        members=tar.getmembers()
        if len(members)!=3 or{m.name for m in members}!=names or any(not m.isfile()or m.size<0 or m.size>2*1024**3 for m in members):raise ValueError('Unexpected backup member')
        by_name={m.name:m for m in members}
        if by_name['manifest.json'].size>1048576:raise ValueError('Backup manifest exceeds bound')
        manifest=json.loads(tar.extractfile(by_name['manifest.json']).read())
        if not isinstance(manifest,dict)or set(manifest)!={'schema','created','members','offhost_verified'}or manifest['schema']!='klikai.pilot.private-backup.v1'or manifest['offhost_verified']is not False or type(manifest['created'])not in{int,float}or not math.isfinite(manifest['created']):raise ValueError('Invalid backup manifest')
        if not isinstance(manifest['members'],dict)or set(manifest['members'])!=names-{'manifest.json'}:raise ValueError('Invalid backup member inventory')
        for name,pin in manifest['members'].items():
            if not isinstance(pin,dict)or set(pin)!={'sha256','bytes'}or type(pin['bytes'])is not int or pin['bytes']<0 or not isinstance(pin['sha256'],str)or not re.fullmatch('[a-f0-9]{64}',pin['sha256'])or by_name[name].size!=pin['bytes']:raise ValueError('Invalid backup member pin: '+name)
            stream=tar.extractfile(by_name[name]);h=hashlib.sha256();length=0
            while chunk:=stream.read(1024*1024):h.update(chunk);length+=len(chunk)
            if length!=pin['bytes']or h.hexdigest()!=pin['sha256']:raise ValueError('Backup member differs: '+name)
        ledger=tar.extractfile(by_name['pilot.deletions.jsonl']);previous='0'*64;sequence=0
        while line:=ledger.readline(8193):
            if len(line)>8192 or not line.endswith(b'\n'):raise ValueError('Invalid deletion ledger line')
            record=json.loads(line);record_hash=record.pop('record_hash');sequence+=1
            from .store import digest
            if record.get('seq')!=sequence or record.get('previous_hash')!=previous or digest(record)!=record_hash:raise ValueError('Deletion ledger chain differs')
            previous=record_hash
    archive_sha=sha(archive);current=archive.lstat()
    if any(getattr(current,k)!=getattr(attrs,k)for k in('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):raise ValueError('Backup changed while being verified')
    return {'archive':str(archive),'sha256':archive_sha,'bytes':current.st_size,'mode':'0600','offhost_verified':False,'manifest':manifest}
