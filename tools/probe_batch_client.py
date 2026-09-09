"""Real HTTP JSONL interrupt/resume acceptance using synthetic public text only.

The last phase installs an explicitly stale synthetic cache identity, then
requires all records to be recomputed against actual HTTP. No resource files,
models, service defaults or operational databases are changed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from linghub_client import LingHubClient, atomic, canonical, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args()
    args.report_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    report = {'schema': 'linghub.batch.http.v1', 'status': 'RUNNING',
              'at': datetime.now(timezone.utc).isoformat(), 'origin': args.base_url,
              'synthetic_inputs_only': True, 'checks': []}
    client = LingHubClient(args.base_url)
    child = None
    try:
        caps = client.request('/v1/capabilities')
        assert caps['engines']['classla']['available'], 'Explicit CLASSLA batch fixture requires available CLASSLA'
        report['capability_identity'] = caps['identity']
        records = [{'id': f'public-{i:02d}', 'lang': 'sl', 'task': 'analyze', 'engine': 'classla',
                    'text': f'Primer {i}. ' + 'Otroci berejo knjige. V Trst je prišla Ana. ' * 4}
                   for i in range(20)]
        input_path, output_dir = args.report_dir / 'input.jsonl', args.report_dir / 'batch'
        def write_input(): atomic(input_path, b''.join(canonical(r) + b'\n' for r in records))
        write_input()
        command = [sys.executable, '-B', str(ROOT / 'linghub_client.py'), '--base-url', args.base_url,
                   'batch', str(input_path), str(output_dir)]
        started = time.monotonic()
        with (args.report_dir / 'interrupt.stdout').open('w') as out, (args.report_dir / 'interrupt.stderr').open('w') as err:
            os.chmod(out.name, 0o600); os.chmod(err.name, 0o600)
            child = subprocess.Popen(command, stdout=out, stderr=err, cwd=ROOT, start_new_session=True)
            manifest_path = output_dir / 'progress.json'
            while child.poll() is None and time.monotonic() - started < 120:
                try: progress = json.loads(manifest_path.read_text())
                except (FileNotFoundError, json.JSONDecodeError): progress = {}
                if len(progress.get('completed', {})) >= 3:
                    child.send_signal(signal.SIGINT)
                    break
                time.sleep(.01)
            code = child.wait(timeout=45)
        progress = json.loads(manifest_path.read_text())
        assert code == 130, ('Expected controlled CLI interruption', code)
        done = len(progress['completed'])
        assert 3 <= done < 20 and progress['status'] == 'interrupted', progress
        report['checks'].append({'name': 'real_sigint', 'status': 'PASS', 'exit_code': code, 'completed': done})

        def resume(name):
            result = subprocess.run(command + ['--resume'], capture_output=True, text=True, timeout=240, cwd=ROOT)
            atomic(args.report_dir / (name + '.stdout'), result.stdout.encode())
            atomic(args.report_dir / (name + '.stderr'), result.stderr.encode())
            assert result.returncode == 0, (name, result.stderr[-200:])
            progress = json.loads(manifest_path.read_text())
            rows = [json.loads(line) for line in (output_dir / 'results.jsonl').read_text().splitlines()]
            assert [r['id'] for r in rows] == [r['id'] for r in records]
            assert all(r['status'] == 'ok' and r['capability_identity'] == caps['identity'] for r in rows)
            assert all(r['result']['text'] == record['text'] for r, record in zip(rows, records))
            assert progress['status'] == 'complete' and progress['failed_ids'] == []
            detail = {'name': name, 'status': 'PASS', 'reused': progress['reused'], 'processed': progress['processed'],
                      'ordered_unique_ids': True, 'input_changed': progress['previous_input_changed'],
                      'identity_changed': progress['previous_identity_changed']}
            report['checks'].append(detail)
            return progress
        progress = resume('resume_interrupted')
        assert progress['reused'] == done and progress['processed'] == 20 - done
        progress = resume('unchanged_resume')
        assert progress['reused'] == 20 and progress['processed'] == 0
        records[0]['text'] += ' To besedilo je spremenjeno.'
        records[1]['engine'] = 'spacy'
        write_input()
        progress = resume('changed_input_and_engine')
        assert progress['reused'] == 18 and progress['processed'] == 2 and progress['previous_input_changed']

        # A previous run under a different resource identity is a cache fixture,
        # not a modification of the currently adopted resource or live response.
        stale_identity = 'synthetic-previous-resource-' + '0' * 64
        progress['capability_identity'] = stale_identity
        for record in records:
            old = progress['completed'][record['id']]
            old_content = (output_dir / 'items' / (old['key'] + '.json')).read_bytes()
            stale_key = digest({'input': record, 'task': record['task'], 'engine': record['engine'],
                                'capability_identity': stale_identity})
            atomic(output_dir / 'items' / (stale_key + '.json'), old_content)
            old['key'] = stale_key
        atomic(manifest_path, canonical(progress) + b'\n')
        progress = resume('stale_resource_identity_recomputed_real_http')
        assert progress['reused'] == 0 and progress['processed'] == 20 and progress['previous_identity_changed']
        report['checks'][-1]['cache_fixture'] = 'Synthetic stale prior resource identity; current HTTP identity and all resources untouched.'
        assert client.request('/v1/capabilities')['identity'] == caps['identity']
        manifest_text = manifest_path.read_text()
        assert all(record['text'] not in manifest_text for record in records)
        assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((output_dir / 'items').stat().st_mode) == 0o700
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in output_dir.rglob('*') if p.is_file())
        report['checks'].append({'name': 'privacy_and_identity_stability', 'status': 'PASS',
                                 'directory_mode': '0700', 'file_mode': '0600', 'raw_text_in_manifest': False,
                                 'current_identity_unchanged': True})
        report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error={'type': type(error).__name__, 'message': str(error)})
    finally:
        if child and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
        atomic(args.report_dir / 'report.json', json.dumps(report, ensure_ascii=False, indent=2).encode() + b'\n')
        print(json.dumps({'status': report['status'], 'checks': len(report['checks']),
                          'report': str(args.report_dir / 'report.json'), 'error': report.get('error')}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
