"""Versioned security migration for a separately reconciled adoption database.

No retired key is installed in the application. Its only use is checking actual
retained bundles during offline inventory. Historical reads address database IDs
and exact stored bytes; they do not authenticate caller-supplied legacy packets.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets
import struct

from .security import Auth, Failure, password_valid
from .store import Store, canonical, digest, stamp

VERSION = 'pilot-security-adoption-v1'
META_KEY = 'recovery_adoption_v1'
CREDENTIAL_FIELDS = ('password_hash', 'must_change', 'password_expires')
RUN_FIELDS = ('id', 'family_id', 'tier', 'model', 'revision', 'parent_run_id',
              'assignment_id', 'state', 'result', 'display_hash', 'created', 'finished')


def _store(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file() or path.parent.resolve() != path.parent:
        raise ValueError('Adoption requires an existing direct private database')
    # Do not run Store.__init__: migration must not create/update schema, replay
    # deletions, alter journal mode, or bootstrap a policy before comparison.
    store = Store.__new__(Store)
    store.path = path
    with store.connect() as db:
        if store.meta('schema', db=db) != 'pilot-v1':
            raise ValueError('Adoption requires the existing pilot-v1 schema')
    return store


def _family_digest(db, family):
    fid = family['id']
    extras = {}
    for table in ('family_focus', 'family_inputs'):
        extras[table] = [dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE family_id=?', (fid,))]
    return digest({'family': dict(family), 'related': extras})


def _typed(value):
    if value is None: return ('null', None)
    if isinstance(value, bytes): return ('blob', value.hex())
    if isinstance(value, str): return ('text', value)
    if isinstance(value, float): return ('real', struct.pack('>d', value).hex())
    if isinstance(value, int): return ('integer', str(value))
    raise ValueError('Unsupported SQLite value type')


def _typed_rows(rows):
    return sorted([tuple(_typed(value) for value in row) for row in rows], key=repr)


def _run_digest(run):
    return digest({k: run[k] for k in RUN_FIELDS})


def inventory(db, legacy_key=None):
    """Only actual retained signed families, plus their exact saved run bytes."""
    rows = {}
    for family in db.execute('SELECT * FROM families ORDER BY id'):
        if not family['bundle']:
            continue
        bundle = json.loads(family['bundle'])
        if not isinstance(bundle, dict) or not {'payload', 'payload_hash', 'signature'} & set(bundle):
            continue  # Existing deterministic CPU bundles have no signature.
        if legacy_key is not None:
            # This is the real KLIKai signer/consumer implementation. The
            # preparer supplies the candidate repository in PYTHONPATH.
            from tools.zrc_reference.pilot_evidence import verify
            verify(bundle, legacy_key)
        if set(bundle) != {'payload', 'payload_hash', 'signature'}:
            raise ValueError('Retained signed bundle shape differs')
        owner = db.execute('SELECT id,deleted FROM accounts WHERE id=?', (family['owner'],)).fetchone()
        if not owner or owner['deleted'] or db.execute('SELECT 1 FROM tombstones WHERE account_id=?', (family['owner'],)).fetchone():
            raise ValueError('Retained signed family refers to a deleted owner')
        runs = {r['id']: _run_digest(r) for r in db.execute('SELECT * FROM runs WHERE family_id=? ORDER BY id', (family['id'],))}
        if db.execute("SELECT 1 FROM runs WHERE family_id=? AND state IN ('queued','running')", (family['id'],)).fetchone():
            raise ValueError('Retained signed family still has unfinished work')
        rows[family['id']] = {'owner': family['owner'], 'family_sha256': _family_digest(db, family),
                              'bundle_bytes_sha256': hashlib.sha256(family['bundle'].encode('utf-8')).hexdigest(),
                              'identity_bytes_sha256': hashlib.sha256((family['identity'] or '').encode('utf-8')).hexdigest(),
                              'runs': runs}
    return {'schema': 'pilot-retained-evidence-v1', 'families': rows,
            'legacy_authenticity': 'exact_retained_record_only; retired_key_exposed; no_fresh_origin_assurance'}


def historical_record(db, family_id, *, owner=None, run_id=None):
    """Check an authenticated stored-record read; never accepts a packet."""
    meta = db.execute('SELECT value FROM meta WHERE key=?', (META_KEY,)).fetchone()
    if not meta:
        return None
    proof = json.loads(meta[0])
    item = proof['inventory']['families'].get(family_id)
    if item is None:
        return None
    family = db.execute('SELECT * FROM families WHERE id=?', (family_id,)).fetchone()
    if not family or (owner is not None and family['owner'] != owner) or family['owner'] != item['owner']:
        raise Failure('not_found', 404)
    account = db.execute('SELECT deleted FROM accounts WHERE id=?', (family['owner'],)).fetchone()
    if not account or account[0] or db.execute('SELECT 1 FROM tombstones WHERE account_id=?', (family['owner'],)).fetchone():
        raise Failure('not_found', 404)
    if _family_digest(db, family) != item['family_sha256']:
        raise Failure('historical_record_changed', 409)
    if run_id is not None:
        run = db.execute('SELECT * FROM runs WHERE id=? AND family_id=?', (run_id, family_id)).fetchone()
        if not run or item['runs'].get(run_id) != _run_digest(run):
            raise Failure('historical_record_changed', 409)
    return item


def historical_source(db, family_id, run_id, owner, evidence_id):
    if historical_record(db, family_id, owner=owner, run_id=run_id) is None:
        return None
    run = db.execute('SELECT result FROM runs WHERE id=?', (run_id,)).fetchone()
    evidence = json.loads(run[0]).get('evidence', [])
    item = next((e for e in evidence if e.get('evidence_id', e.get('passage_id', e.get('citation_id'))) == evidence_id), None)
    if item is None:
        raise Failure('not_found', 404)
    # Return the exact saved excerpt; no regeneration, re-signing or old-key API.
    if 'citation_id' in item:
        return {'evidence': [item], 'historical_record': True}
    return {k: item[k] for k in ('evidence_id', 'source_id', 'source_revision',
        'source_label', 'excerpt', 'locator', 'url', 'status', 'edition',
        'bibliography', 'title', 'publication_scope', 'annual_scope') if k in item}


def migrate_adoption(store_path, *, operator_id=None, operator_password,
                     migration_id, source_identity_sha256, receipt_sha256s,
                     legacy_key, hasher=None):
    """Atomically remediate a caller-created, all-table-equal adoption copy.

    A repeat checks its pinned migration inputs and operator password, then
    returns the same receipt without writing or rotating any credential.
    """
    password_valid(operator_password)
    if not isinstance(migration_id, str) or not 8 <= len(migration_id) <= 150:
        raise ValueError('Invalid security migration identity')
    if not isinstance(legacy_key, bytes) or len(legacy_key) < 32:
        raise ValueError('Retained key is required for offline inventory')
    store = _store(store_path)
    auth = Auth(store, hasher)
    input_identity = {'migration_id': migration_id, 'source_identity_sha256': source_identity_sha256,
                      'receipt_sha256s': receipt_sha256s}
    with store.tx() as db:
        prior = store.meta(META_KEY, db=db)
        if prior:
            proof = json.loads(prior)
            if any(proof.get(k) != v for k, v in input_identity.items()) or (operator_id is not None and proof['operator_id'] != operator_id):
                raise ValueError('Existing adoption migration input conflict')
            row = db.execute('SELECT password_hash FROM accounts WHERE id=?', (proof['operator_id'],)).fetchone()
            if not row or not auth.verify(operator_password, row[0]):
                raise ValueError('Existing adoption operator credential differs')
            if inventory(db) != proof['inventory']:
                raise ValueError('Existing adoption historical inventory differs')
            return proof
        eligible = list(db.execute("SELECT * FROM accounts WHERE origin='human' AND role='admin' AND enabled=1 AND deleted=0"))
        if operator_id is None:
            if len(eligible) != 1:
                raise ValueError('Exactly one retained human administrator or explicit operator_id required')
            operator_id = eligible[0]['id']
        if operator_id not in {r['id'] for r in eligible}:
            raise ValueError('Operator must be an existing enabled human administrator')
        historical = inventory(db, legacy_key)
        accounts = list(db.execute('SELECT * FROM accounts ORDER BY id'))
        if any(auth.verify(operator_password, r['password_hash']) for r in accounts):
            raise ValueError('Fresh operator credential must differ from every retained credential')
        revoked = db.execute('SELECT count(*) FROM sessions').fetchone()[0]
        now = stamp()
        for row in accounts:
            password = operator_password if row['id'] == operator_id else secrets.token_urlsafe(64)
            expiry = now + 86400 if row['id'] == operator_id else 1.0
            db.execute('UPDATE accounts SET password_hash=?,must_change=1,password_expires=? WHERE id=?',
                       (auth.hasher.hash(password), expiry, row['id']))
        db.execute('DELETE FROM sessions')
        proof = {'version': VERSION, **input_identity, 'operator_id': operator_id,
                 'accounts_remediated': len(accounts), 'sessions_revoked': revoked,
                 'credential_fields': list(CREDENTIAL_FIELDS), 'operator_password_expires': now + 86400,
                 'inventory': historical, 'inventory_sha256': digest(historical),
                 'audit_event': 'security_adoption', 'audit_created': now,
                 'password_delivery': 'individual_private_file; forced_change; no_password_in_receipt'}
        detail = {'migration_id': migration_id, 'source_identity_sha256': source_identity_sha256,
                  'accounts_remediated': len(accounts), 'sessions_revoked': revoked,
                  'inventory_sha256': proof['inventory_sha256']}
        db.execute('INSERT INTO audit(event,account_id,detail,created) VALUES(?,?,?,?)',
                   ('security_adoption', operator_id, canonical(detail), now))
        proof['audit_seq'] = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        proof['audit_detail'] = detail
        db.execute('INSERT INTO meta VALUES(?,?)', (META_KEY, canonical(proof)))
    return proof


def validate_adoption(before_path, after_path, receipt):
    """Independently prove the complete declared migration delta, including IDs."""
    before, after = _store(before_path), _store(after_path)
    with before.connect() as old, after.connect() as new:
        old.execute('BEGIN'); new.execute('BEGIN')
        tables = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        if tables != [r[0] for r in new.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]:
            raise ValueError('Adoption table set changed')
        for query in ("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name", 'PRAGMA user_version', 'PRAGMA application_id'):
            if _typed_rows(old.execute(query)) != _typed_rows(new.execute(query)):
                raise ValueError('Adoption schema/version changed')
        for table in tables:
            quoted='\"'+table.replace('\"','\"\"')+'\"'
            if _typed_rows(old.execute('PRAGMA table_xinfo('+quoted+')')) != _typed_rows(new.execute('PRAGMA table_xinfo('+quoted+')')):
                raise ValueError('Adoption column/type information changed')
            rows_old = [tuple(r) for r in old.execute('SELECT * FROM "'+table.replace('"','""')+'"')]
            rows_new = [tuple(r) for r in new.execute('SELECT * FROM "'+table.replace('"','""')+'"')]
            if table == 'accounts':
                left = {r['id']: dict(r) for r in old.execute('SELECT * FROM accounts')}
                right = {r['id']: dict(r) for r in new.execute('SELECT * FROM accounts')}
                if left.keys() != right.keys(): raise ValueError('Adoption account IDs changed')
                for aid, row in left.items():
                    updated = right[aid]
                    if any(_typed(row[k]) != _typed(updated[k]) for k in row if k not in CREDENTIAL_FIELDS): raise ValueError('Adoption noncredential account field changed')
                    if row['password_hash'] == updated['password_hash'] or not updated['password_hash'].startswith('$argon2id$') or updated['must_change'] != 1:
                        raise ValueError('Adoption account credential remediation differs')
                    expected = receipt['operator_password_expires'] if aid == receipt['operator_id'] else 1.0
                    if updated['password_expires'] != expected: raise ValueError('Adoption credential expiry differs')
            elif table == 'sessions':
                if rows_new or len(rows_old) != receipt['sessions_revoked']: raise ValueError('Adoption sessions not fully revoked')
            elif table == 'meta':
                expected = rows_old + [(META_KEY, canonical(receipt))]
                if sorted(rows_new) != sorted(expected): raise ValueError('Adoption meta mutation differs')
            elif table == 'audit':
                extra = (receipt['audit_seq'], 'security_adoption', receipt['operator_id'], canonical(receipt['audit_detail']), receipt['audit_created'])
                if rows_new != rows_old + [extra]: raise ValueError('Adoption audit append differs')
            elif table == 'sqlite_sequence':
                left, right = dict(rows_old), dict(rows_new)
                expected_seq = max(left.get('audit', 0), max((r[0] for r in old.execute('SELECT seq FROM audit')), default=0)) + 1
                if receipt['audit_seq'] != expected_seq: raise ValueError('Adoption audit sequence differs')
                left['audit'] = expected_seq
                if right != left: raise ValueError('Adoption sequence mutation differs')
            elif _typed_rows(rows_old) != _typed_rows(rows_new):
                raise ValueError('Adoption undeclared table mutation: '+table)
        if inventory(old) != receipt['inventory'] or inventory(new) != receipt['inventory']:
            raise ValueError('Adoption retained inventory differs')
        if digest(receipt['inventory']) != receipt['inventory_sha256']:
            raise ValueError('Adoption inventory digest differs')
        if len(list(old.execute('SELECT id FROM accounts'))) != receipt['accounts_remediated']:
            raise ValueError('Adoption account count differs')
    return {'status': 'PASS', 'version': VERSION, 'all_tables_checked': tables,
            'noncredential_account_fields_preserved': True, 'retained_bytes_preserved': True,
            'sessions_revoked': receipt['sessions_revoked'], 'audit_rows_appended': 1,
            'inventory_sha256': receipt['inventory_sha256']}
