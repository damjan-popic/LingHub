"""Synthetic retained-to-adoption browser fixture; no service/model dependencies."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.directory.mkdir(mode=0o700)
    subprocess.run(['setfacl', '-b', '-k', str(args.directory)], check=True)
    args.directory.chmod(0o700)
    from pilot_backend.api import create_app
    from pilot_backend.adoption import migrate_adoption, validate_adoption
    from pilot_backend.adoption_probe import SignedFixtureAdapter
    old_key, new_key = secrets.token_bytes(48), secrets.token_bytes(48)
    old_password, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    before, after = args.directory/'before', args.directory/'store'
    origin = f'http://127.0.0.1:{args.port}'
    config = {'store': str(before), 'allowed_origins': [origin], 'secure_cookies': False, 'human_testing_mode': True}
    old = create_app(config, adapter=SignedFixtureAdapter(old_key), start_workers=False)
    aid = old.state.auth.create('fixture-adoption-operator', old_password, role='admin', origin='human', must_change=False)
    token, _ = old.state.auth.login('fixture-adoption-operator', old_password, 'synthetic-browser')
    saved = old.state.domain.create_family(aid, {'question': 'SYNTHETIC retained browser question', 'task': 'reference', 'model': 'qwen', 'idempotency_key': 'browser-retained-family'})
    old.state.jobs.execute(old.state.jobs.claim())
    source, target = sqlite3.connect(before), sqlite3.connect(after)
    try: source.backup(target)
    finally: target.close(); source.close()
    proof = migrate_adoption(after, operator_id=aid, operator_password=password, migration_id='synthetic-browser-adoption', source_identity_sha256='1'*64, receipt_sha256s={'backup': '2'*64, 'preflight': '3'*64}, legacy_key=old_key)
    validate_adoption(before, after, proof)
    credentials = {'username': 'fixture-adoption-operator', 'password': password, 'old_password': old_password,
                   'old_session': token, 'saved_family': saved['family']['answer_family_id'],
                   'permanent_password': secrets.token_urlsafe(32)}
    fd = os.open(args.directory/'accounts.json', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out: json.dump(credentials, out)
    app = create_app(dict(config, store=str(after)), adapter=SignedFixtureAdapter(new_key))
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__': main()
