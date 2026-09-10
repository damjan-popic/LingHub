"""Private durable pilot records with foreign keys and deletion replay."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def uid():return str(uuid.uuid4())
def stamp():return time.time()

SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,username TEXT NOT NULL,username_key TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('tester','reviewer','admin')),cohort TEXT NOT NULL,origin TEXT NOT NULL CHECK(origin IN ('human','synthetic','imported','test')),enabled INTEGER NOT NULL DEFAULT1,must_change INTEGER NOT NULL DEFAULT1,password_expires REAL,created REAL NOT NULL,deleted INTEGER NOT NULL DEFAULT0);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,csrf TEXT NOT NULL,created REAL NOT NULL,last_seen REAL NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(key TEXT NOT NULL,at REAL NOT NULL,success INTEGER NOT NULL,PRIMARY KEY(key,at));
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT NOT NULL,account_id TEXT,detail TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS policies(id TEXT PRIMARY KEY,config TEXT NOT NULL,parent_id TEXT REFERENCES policies(id),created REAL NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS families(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,question TEXT NOT NULL,context TEXT NOT NULL,task TEXT NOT NULL,locale TEXT NOT NULL,interface_locale TEXT NOT NULL,input_language TEXT NOT NULL,model TEXT NOT NULL,policy_id TEXT NOT NULL REFERENCES policies(id),bundle TEXT,identity TEXT,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assignments(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,blind INTEGER NOT NULL,mapping TEXT NOT NULL,revealed REAL,prior_named_exposure INTEGER NOT NULL,active_tier TEXT NOT NULL DEFAULT 'short',created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,tier TEXT NOT NULL CHECK(tier IN ('short','long')),model TEXT NOT NULL,revision INTEGER NOT NULL,parent_run_id TEXT REFERENCES runs(id),assignment_id TEXT REFERENCES assignments(id) ON DELETE CASCADE,state TEXT NOT NULL,result TEXT,display_hash TEXT,created REAL NOT NULL,finished REAL,delivered REAL,UNIQUE(family_id,tier,model,assignment_id,revision));
CREATE TABLE IF NOT EXISTS assignment_runs(assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,tier TEXT NOT NULL,side TEXT NOT NULL CHECK(side IN ('A','B')),run_id TEXT UNIQUE NOT NULL REFERENCES runs(id) ON DELETE CASCADE,PRIMARY KEY(assignment_id,tier,side));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,run_id TEXT UNIQUE NOT NULL REFERENCES runs(id) ON DELETE CASCADE,owner TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,kind TEXT NOT NULL,model TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,started REAL,finished REAL,cancel_requested INTEGER NOT NULL DEFAULT0,error_code TEXT,attempts INTEGER NOT NULL DEFAULT0);
CREATE INDEX IF NOT EXISTS pending_jobs ON jobs(state,created);
CREATE TABLE IF NOT EXISTS request_keys(owner TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,scope TEXT NOT NULL,key TEXT NOT NULL,payload_hash TEXT NOT NULL,response TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(owner,scope,key));
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,display_hash TEXT NOT NULL,expanded INTEGER NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,revision INTEGER NOT NULL DEFAULT0,UNIQUE(owner,run_id));
CREATE TABLE IF NOT EXISTS feedback_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,feedback_id TEXT NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,revision INTEGER NOT NULL,rating TEXT,tags TEXT NOT NULL,comment TEXT NOT NULL,correction TEXT NOT NULL,anchor TEXT,display_hash TEXT NOT NULL,created REAL NOT NULL,target TEXT NOT NULL,UNIQUE(feedback_id,revision));
CREATE TABLE IF NOT EXISTS feedback_triage(id TEXT PRIMARY KEY,feedback_id TEXT NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,actor TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,status TEXT NOT NULL,note TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS votes(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,owner TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,revision INTEGER NOT NULL,tier TEXT NOT NULL,choice TEXT NOT NULL,expanded INTEGER NOT NULL,created REAL NOT NULL,UNIQUE(assignment_id,revision));
CREATE TABLE IF NOT EXISTS review_assignments(reviewer TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,feedback_id TEXT NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,PRIMARY KEY(reviewer,feedback_id));
CREATE TABLE IF NOT EXISTS cycles(id TEXT PRIMARY KEY,kind TEXT NOT NULL,cutoff INTEGER NOT NULL,previous_cutoff INTEGER NOT NULL,input_hash TEXT NOT NULL,status TEXT NOT NULL,report TEXT NOT NULL,created REAL NOT NULL,finished REAL);
CREATE TABLE IF NOT EXISTS canaries(id TEXT PRIMARY KEY,cycle_id TEXT UNIQUE NOT NULL REFERENCES cycles(id),baseline TEXT NOT NULL REFERENCES policies(id),candidate TEXT NOT NULL REFERENCES policies(id),state TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,completed_runs INTEGER NOT NULL DEFAULT0,baseline_error REAL NOT NULL DEFAULT0,critical_failure INTEGER NOT NULL DEFAULT0);
CREATE TRIGGER IF NOT EXISTS family_identity_immutable BEFORE UPDATE OF owner,question,context,task,locale,interface_locale,input_language,model,policy_id,created ON families BEGIN SELECT RAISE(ABORT,'Family identity is immutable');END;
CREATE TRIGGER IF NOT EXISTS family_freeze_once BEFORE UPDATE OF bundle,identity ON families WHEN OLD.bundle IS NOT NULL OR OLD.identity IS NOT NULL BEGIN SELECT RAISE(ABORT,'Family evidence is frozen');END;
CREATE TRIGGER IF NOT EXISTS run_result_immutable BEFORE UPDATE OF result,display_hash,family_id,tier,model,revision,parent_run_id,assignment_id ON runs WHEN OLD.state IN ('complete','failed','cancelled') BEGIN SELECT RAISE(ABORT,'Finished run is immutable');END;
CREATE TRIGGER IF NOT EXISTS assignment_mapping_immutable BEFORE UPDATE OF owner,family_id,blind,mapping,created ON assignments BEGIN SELECT RAISE(ABORT,'Comparison assignment is immutable');END;
CREATE TRIGGER IF NOT EXISTS policy_config_immutable BEFORE UPDATE OF config,parent_id ON policies BEGIN SELECT RAISE(ABORT,'Policy configuration is immutable');END;
CREATE TRIGGER IF NOT EXISTS feedback_revision_immutable BEFORE UPDATE ON feedback_events BEGIN SELECT RAISE(ABORT,'Feedback revisions are immutable');END;
CREATE TRIGGER IF NOT EXISTS vote_revision_immutable BEFORE UPDATE ON votes BEGIN SELECT RAISE(ABORT,'Vote revisions are immutable');END;
CREATE TABLE IF NOT EXISTS tombstones(seq INTEGER PRIMARY KEY,account_id TEXT UNIQUE NOT NULL,created REAL NOT NULL,previous_hash TEXT NOT NULL,record_hash TEXT NOT NULL);
'''.replace('DEFAULT1','DEFAULT 1').replace('DEFAULT0','DEFAULT 0')

BASE_POLICY={'rerank_policy':'always_for_answers','lexical_weight':0.5,'context_passage_limit':10,'short_style_preset':'direct_qualified_v1','long_style_preset':'explanation_examples_v1'}

class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

class Store:
    def __init__(self,path):
        self.path=Path(path).absolute()
        if self.path.name in {'fulltext.sqlite','vectors.sqlite'}:raise ValueError('Use a separate pilot account store')
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.path.is_symlink()or self.path.parent.resolve()!=self.path.parent:raise ValueError('Pilot store path must be direct')
        if not self.path.exists():
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with self.connect()as db:
            tables={r[0]for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                row=db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()if 'meta'in tables else None
                if not row or row[0]!='pilot-v1':raise ValueError('Existing database is not the pilot store')
            os.chmod(self.path,0o600)
            db.execute('PRAGMA journal_mode=WAL');db.executescript('BEGIN IMMEDIATE;\n'+SCHEMA)
            db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('schema','pilot-v1'))
            db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('audit_salt',secrets.token_hex(32)))
            pid=digest(BASE_POLICY);db.execute('INSERT OR IGNORE INTO policies VALUES(?,?,?,?,?)',(pid,canonical(BASE_POLICY),None,stamp(),'baseline'))
            db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('active_policy',pid))
        self.replay_deletions()
    def connect(self):
        db=sqlite3.connect(self.path,timeout=5,factory=ClosingConnection);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=5000');return db
    @contextmanager
    def tx(self):
        db=self.connect()
        try:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def meta(self,key,default=None,db=None):
        if db is not None:
            r=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone();return r[0]if r else default
        with self.connect()as con:return self.meta(key,default,con)
    def audit(self,event,account=None,detail=None,db=None):
        if db is None:
            with self.tx()as con:self.audit(event,account,detail,con)
        else:db.execute('INSERT INTO audit(event,account_id,detail,created)VALUES(?,?,?,?)',(event,account,canonical(detail or{}),stamp()))
    @property
    def deletion_path(self):return self.path.with_suffix('.deletions.jsonl')
    def deletion_records(self):
        if not self.deletion_path.exists():return []
        rows=[];previous='0'*64
        for line in self.deletion_path.read_text().splitlines():
            row=json.loads(line);h=row.pop('record_hash')
            if row['previous_hash']!=previous or row['seq']!=len(rows)+1 or digest(row)!=h:raise ValueError('Deletion ledger chain differs')
            rows.append(dict(row,record_hash=h));previous=h
        return rows
    def replay_deletions(self):
        rows=self.deletion_records()
        with self.tx()as db:
            for r in rows:
                db.execute('INSERT OR IGNORE INTO tombstones VALUES(?,?,?,?,?)',(r['seq'],r['account_id'],r['created'],r['previous_hash'],r['record_hash']))
                if r.get('kind')=='retention':self._expire(db,r['cutoff'])
                else:db.execute('DELETE FROM accounts WHERE id=?',(r['account_id'],))
    def delete_account(self,account_id):
        lock=self.path.with_suffix('.deletion.lock')
        fd=os.open(lock,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        with os.fdopen(fd,'a')as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            rows=self.deletion_records();previous=rows[-1]['record_hash']if rows else'0'*64
            if not any(r['account_id']==account_id for r in rows):
                r={'seq':len(rows)+1,'account_id':account_id,'created':stamp(),'previous_hash':previous};r['record_hash']=digest(r)
                fd=os.open(self.deletion_path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
                with os.fdopen(fd,'a')as out:out.write(canonical(r)+'\n');out.flush();os.fsync(out.fileno())
            self.replay_deletions()
    def _expire(self,db,cutoff):
        db.execute('DELETE FROM feedback_events WHERE created<=?',(cutoff,))
        db.execute('DELETE FROM feedback WHERE NOT EXISTS(SELECT 1 FROM feedback_events e WHERE e.feedback_id=feedback.id)')
        db.execute('DELETE FROM votes WHERE created<=?',(cutoff,))
        db.execute('DELETE FROM deliveries WHERE created<=?',(cutoff,))
        db.execute('DELETE FROM request_keys WHERE created<=?',(cutoff,))
        db.execute("DELETE FROM families WHERE created<=?AND NOT EXISTS(SELECT 1 FROM runs r WHERE r.family_id=families.id AND (r.finished>?OR r.state IN ('running','queued')))AND NOT EXISTS(SELECT 1 FROM feedback f JOIN runs r ON r.id=f.run_id WHERE r.family_id=families.id)",(cutoff,cutoff))
    def retention(self,days=180,now=None):
        if days!=180:raise ValueError('Pilot retention is fixed at180 days')
        cutoff=(stamp()if now is None else now)-days*86400
        fd=os.open(self.path.with_suffix('.deletion.lock'),os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        with os.fdopen(fd,'a')as stream:
            fcntl.flock(stream,fcntl.LOCK_EX);rows=self.deletion_records()
            if not any(r.get('kind')=='retention'and r['cutoff']>=cutoff for r in rows):
                r={'seq':len(rows)+1,'account_id':'retention:'+str(len(rows)+1),'kind':'retention','cutoff':cutoff,'created':stamp(),'previous_hash':rows[-1]['record_hash']if rows else'0'*64};r['record_hash']=digest(r)
                outfd=os.open(self.deletion_path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
                with os.fdopen(outfd,'a')as out:out.write(canonical(r)+'\n');out.flush();os.fsync(out.fileno())
            self.replay_deletions()
        return {'status':'retention_applied','days':days,'cutoff':cutoff,'deletion_ledger_length':len(self.deletion_records())}
    @contextmanager
    def account_lock(self):
        fd=os.open(self.path.with_suffix('.accounts.lock'),os.O_CREAT|os.O_RDWR,0o600)
        with os.fdopen(fd,'a')as out:
            fcntl.flock(out,fcntl.LOCK_EX)
            yield
    def recover_jobs(self):
        with self.tx()as db:
            ids=[r[0]for r in db.execute("SELECT run_id FROM jobs WHERE state='running'")]
            db.execute("UPDATE jobs SET state='failed',finished=?,error_code='interrupted'WHERE state='running'",(stamp(),))
            db.executemany("UPDATE runs SET state='failed',finished=? WHERE id=?",[(stamp(),i)for i in ids])
