"""Maintained Argon2id plus revocable opaque server-side sessions."""
from __future__ import annotations
import hashlib
import hmac
import re
import secrets
import unicodedata
from argon2 import PasswordHasher,Type
from argon2.exceptions import VerificationError,InvalidHashError
from .store import canonical,digest,stamp,uid

class Failure(Exception):
    def __init__(self,code,status=400):self.code=code;self.status=status;super().__init__(code)


def username_key(value):
    value=unicodedata.normalize('NFKC',value).casefold().strip()
    if not re.fullmatch(r'[\w.@+-]{2,80}',value):raise Failure('invalid_username')
    return value

def password_valid(value):
    if not isinstance(value,str)or not 12<=len(value)<=1024 or '\0'in value:raise Failure('password_length')
    try:value.encode('utf-8')
    except UnicodeError:raise Failure('invalid_request')from None


def token_hash(value):return hashlib.sha256(value.encode()).hexdigest()

class Auth:
    remembered_seconds = 30 * 86400
    def __init__(self,store,hasher=None,idle_seconds=1800,absolute_seconds=28800):
        self.store=store;self.hasher=hasher or PasswordHasher(time_cost=3,memory_cost=65536,parallelism=1,type=Type.ID)
        self.idle=idle_seconds;self.absolute=absolute_seconds;self.dummy=self.hasher.hash(secrets.token_urlsafe(32))
    def create(self,username,password,role='tester',cohort='pilot',origin='human',must_change=True,bootstrap_id=None):
        key=username_key(username);password_valid(password)
        if not isinstance(role,str)or not isinstance(origin,str)or role not in {'tester','reviewer','admin'}or origin not in {'human','synthetic','imported','test'}:raise Failure('invalid_request')
        account=uid();hashed=self.hasher.hash(password)
        import sqlite3
        try:
            with self.store.tx()as db:
                if bootstrap_id is not None:
                    if not isinstance(bootstrap_id,str)or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',bootstrap_id)or role!='admin':raise Failure('invalid_bootstrap')
                    old=self.store.meta('admin_bootstrap',db=db)
                    if old:
                        import json
                        prior=json.loads(old)
                        if prior['bootstrap_id']!=bootstrap_id or prior['username_key']!=key:raise Failure('bootstrap_conflict',409)
                        if not db.execute("SELECT 1 FROM accounts WHERE id=?AND role='admin'",(prior['account_id'],)).fetchone():raise Failure('bootstrap_account_changed',409)
                        return prior['account_id']
                    if db.execute("SELECT 1 FROM accounts WHERE role='admin'").fetchone():raise Failure('bootstrap_conflict',409)
                db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(account,username.strip(),key,hashed,role,cohort,origin,1,int(must_change),stamp()+86400 if must_change else None,stamp(),0))
                if bootstrap_id is not None:db.execute('INSERT INTO meta VALUES(?,?)',('admin_bootstrap',canonical({'bootstrap_id':bootstrap_id,'username_key':key,'account_id':account})))
        except sqlite3.IntegrityError:raise Failure('username_unavailable',409)from None
        return account
    def verify(self,password,encoded):
        try:return self.hasher.verify(encoded,password)
        except (VerificationError,InvalidHashError,ValueError,TypeError):return False
    def throttle_keys(self,username,source):
        salt=self.store.meta('audit_salt')
        return ['account:'+hmac.new(salt.encode(),username.encode(),hashlib.sha256).hexdigest(),'source:'+hmac.new(salt.encode(),source.encode(),hashlib.sha256).hexdigest()]
    def login(self,username,password,source,remember=False):
        if type(remember)is not bool:raise Failure('invalid_request')
        try:key=username_key(username)
        except Failure:key='invalid'
        keys=self.throttle_keys(key,source);now=stamp()
        with self.store.tx()as db:
            db.execute('DELETE FROM login_attempts WHERE at<?',(now-900,))
            for k,limit in zip(keys,(8,30)):
                if db.execute('SELECT count(*) FROM login_attempts WHERE key=? AND at>? AND success=0',(k,now-900)).fetchone()[0]>=limit:raise Failure('login_throttled',429)
            row=db.execute('SELECT * FROM accounts WHERE username_key=?',(key,)).fetchone()
        valid=self.verify(password,row['password_hash']if row else self.dummy)
        allowed=valid and row and row['enabled']and not row['deleted']and(not row['password_expires']or row['password_expires']>now)
        with self.store.tx()as db:
            for k in keys:db.execute('INSERT INTO login_attempts VALUES(?,?,?)',(k,now,int(bool(allowed))))
            self.store.audit('login_success'if allowed else'login_failure',row['id']if allowed else None,{},db)
        if not allowed:raise Failure('login_failed',401)
        if self.hasher.check_needs_rehash(row['password_hash']):
            with self.store.tx()as db:db.execute('UPDATE accounts SET password_hash=?WHERE id=?',(self.hasher.hash(password),row['id']))
        return self.issue(row['id'],remember=remember)
    def issue(self,account,remember=False):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);now=stamp()
        with self.store.tx()as db:
            owner=db.execute('SELECT role,must_change FROM accounts WHERE id=?',(account,)).fetchone()
            # Only ordinary tester accounts can opt in. Privileged and temporary
            # credentials retain the existing short session policy.
            remembered=bool(remember and owner and owner['role']=='tester'and not owner['must_change'])
            expires=now+(self.remembered_seconds if remembered else self.absolute)
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(token_hash(token),account,csrf,now,now,expires))
            if remembered:db.execute('INSERT INTO remembered_sessions VALUES(?)',(token_hash(token),))
        return token,self.session(token)
    def session(self,token,touch=True):
        if not token or len(token)>200:raise Failure('login_required',401)
        now=stamp()
        with self.store.tx()as db:
            row=db.execute('SELECT a.*,s.csrf,s.token_hash,s.created AS session_created,s.last_seen,s.expires,EXISTS(SELECT 1 FROM remembered_sessions r WHERE r.token_hash=s.token_hash) AS remembered FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=?',(token_hash(token),)).fetchone()
            remembered=bool(row and row['remembered']and row['role']=='tester'and not row['must_change'])
            if not row or not row['enabled']or row['deleted']or row['expires']<=now or(not remembered and row['last_seen']<=now-self.idle)or(row['remembered']and not remembered):
                db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token),));db.commit();raise Failure('login_required',401)
            if touch:db.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?',(now,token_hash(token)))
            return dict(row)
    def logout(self,token):
        with self.store.tx()as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token or''),))
    def change_password(self,account,current,new):
        password_valid(new)
        with self.store.connect()as db:row=db.execute('SELECT * FROM accounts WHERE id=?',(account,)).fetchone()
        if not row or not self.verify(current,row['password_hash']):raise Failure('reauth_failed',403)
        self.reset_password(account,new,temporary=False);return self.issue(account)
    def reset_password(self,account,new,temporary=True):
        password_valid(new);hashed=self.hasher.hash(new)
        with self.store.tx()as db:
            db.execute('UPDATE accounts SET password_hash=?,must_change=?,password_expires=? WHERE id=?',(hashed,int(temporary),stamp()+86400 if temporary else None,account))
            db.execute('DELETE FROM sessions WHERE account_id=?',(account,))
    def reauthenticate(self,account,password):
        with self.store.connect()as db:row=db.execute('SELECT password_hash FROM accounts WHERE id=?',(account,)).fetchone()
        if not row or not self.verify(password,row[0]):raise Failure('reauth_failed',403)
