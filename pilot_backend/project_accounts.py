"""Idempotent operator provisioning of the two shared project logins.

The private credential note is written before the account transaction, so a
retry after interruption uses the same passwords and IDs. Existing accounts
are verified, never reset or silently adopted.
"""
import json
import os
from pathlib import Path
import secrets
import uuid

from .security import Auth, Failure, password_valid, username_key
from .store import canonical, stamp, uid

USERNAMES = ('cu', 'slori')
COHORT = 'project_beta'


def provision(store, credentials_file, *, hasher=None):
    path = Path(credentials_file)
    code_root = Path(__file__).resolve().parents[1]
    if (not path.is_absolute() or path.is_symlink() or path.parent.resolve() != path.parent
            or code_root in path.parents or not path.parent.is_dir()):
        raise ValueError('Use an existing private directory outside application code for credentials')
    auth = Auth(store, hasher=hasher)
    with store.account_lock():
        if not path.exists():
            with store.connect() as db:
                if db.execute('SELECT 1 FROM accounts WHERE username_key IN (?,?)', USERNAMES).fetchone():
                    raise Failure('project_account_credentials_required', 409)
            note = {'schema': 'klikai.project-accounts.v1', 'accounts': [
                {'username': name, 'account_id': uid(), 'password': secrets.token_urlsafe(24)}
                for name in USERNAMES]}
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                stream.write(canonical(note) + '\n');stream.flush();os.fsync(stream.fileno())
        if path.stat().st_mode & 0o077 or path.stat().st_uid != os.geteuid():
            raise ValueError('Credential note must be an owned protected file')
        note = json.loads(path.read_text())
        rows = note.get('accounts', []) if isinstance(note, dict) else []
        if (not isinstance(note, dict) or note.get('schema') != 'klikai.project-accounts.v1'
                or not isinstance(rows, list) or [r.get('username') for r in rows if isinstance(r, dict)] != list(USERNAMES)
                or len(rows) != 2 or any(not isinstance(r,dict) or not isinstance(r.get('account_id'), str) for r in rows)
                or len({r['account_id'] for r in rows}) != 2):
            raise Failure('project_account_credentials_invalid', 409)
        try:
            if any(str(uuid.UUID(r['account_id'])) != r['account_id'] for r in rows):raise ValueError
        except ValueError:raise Failure('project_account_credentials_invalid', 409)from None
        for row in rows:password_valid(row.get('password'))
        if rows[0]['password'] == rows[1]['password']:raise Failure('project_account_credentials_invalid', 409)
        results = []
        with store.tx() as db:
            for row in rows:
                old = db.execute('SELECT * FROM accounts WHERE username_key=?', (username_key(row['username']),)).fetchone()
                if old:
                    if (old['id'] != row['account_id'] or old['role'] != 'tester' or old['cohort'] != COHORT
                            or old['origin'] != 'human' or not old['enabled'] or old['deleted']
                            or old['must_change'] or old['password_expires'] is not None
                            or not auth.verify(row['password'], old['password_hash'])):
                        raise Failure('project_account_identity_conflict', 409)
                    status = 'existing'
                else:
                    if db.execute('SELECT 1 FROM tombstones WHERE account_id=?', (row['account_id'],)).fetchone():
                        raise Failure('project_account_identity_conflict', 409)
                    db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (row['account_id'], row['username'], row['username'], auth.hasher.hash(row['password']),
                         'tester', COHORT, 'human', 1, 0, None, stamp(), 0))
                    store.audit('project_account_created', row['account_id'], {'username': row['username']}, db)
                    status = 'created'
                results.append({'username': row['username'], 'account_id': row['account_id'], 'status': status})
    return {'status': 'ready', 'accounts': results, 'credentials_file': str(path), 'must_change_password': False}
