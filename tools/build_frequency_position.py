#!/usr/bin/env python3
"""Build a separate immutable head-frequency inventory; never edit source data."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.lexical_identity import source_occurrence_key
from pilot_backend.frequency_position import CORPUS, VERSION, observation_value


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build(source, output):
    source, output = Path(source).resolve(strict=True), Path(output).absolute()
    if output.exists():
        raise ValueError('Choose a new immutable output directory')
    manifest_path, database = source / 'manifest.json', source / 'sloleks.sqlite'
    original = json.loads(manifest_path.read_text())
    if (original.get('status'), original.get('resource_id'), original.get('version')) != ('READY', 'sloleks', '3.1'):
        raise ValueError('Expected complete retained Sloleks 3.1 profiles')
    if database.is_symlink() or sha(database) != original['database_sha256']:
        raise ValueError('Source database digest differs')
    before = database.stat()
    started = time.monotonic()
    recipe = {'version': VERSION, 'corpus': CORPUS, 'scope': 'lexeme', 'unit': 'occurrences',
        'population_unit': 'source_lexeme_occurrence', 'source_manifest_sha256': sha(manifest_path),
        'source_database_sha256': original['database_sha256'],
        'source_archive_sha256': original['source_archive_sha256'],
        'builder_sha256': sha(Path(__file__)),
        'observation_recipe_sha256': sha(Path(__file__).resolve().parents[1] / 'pilot_backend/frequency_position.py'),
        'rank_definition': 'Descending tied rank interval: 1+greater_count through greater_count+equal_count.',
        'percentile_definition': 'Empirical frequency CDF tie block (100*less/N,100*(less+equal)/N]; midpoint is 100*(less+equal/2)/N.',
        'quartile_definition': 'Q1=(0,25],Q2=(25,50],Q3=(50,75],Q4=(75,100]; retain every quartile touched by a tie block, never split ties.'}
    build_id = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.building-' + str(os.getpid()))
    temporary.mkdir(mode=0o700)
    target = temporary / 'frequency-position.sqlite'
    db = sqlite3.connect(target)
    db.executescript('''PRAGMA user_version=1; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE positions(occurrence_key TEXT PRIMARY KEY,source_entry_id TEXT NOT NULL,
          lemma TEXT NOT NULL,grammatical_type TEXT,grammar_json TEXT NOT NULL,source_status TEXT,
          frequency INTEGER,state TEXT NOT NULL,observations_json TEXT NOT NULL,
          rank_min INTEGER,rank_max INTEGER,less_count INTEGER,equal_count INTEGER,
          quartile_min INTEGER,quartile_max INTEGER);
        CREATE INDEX frequency_values ON positions(frequency) WHERE state='eligible';''')
    db.execute('INSERT INTO metadata VALUES (?,?)', ('build_id', build_id))
    counts = Counter(source_entries=0, eligible=0, zero=0, missing=0, invalid=0, conflicting=0)
    shard_counts = Counter()
    src = sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)
    src.execute('PRAGMA query_only=ON')
    try:
        for (encoded,) in src.execute('SELECT profile FROM entries ORDER BY id'):
            entry = json.loads(zlib.decompress(encoded))
            provenance = entry['evidence']
            key = source_occurrence_key(provenance)
            if (not key or provenance.get('source_archive_sha256') != original['source_archive_sha256']
                    or provenance.get('resource_id') != 'sloleks' or provenance.get('version') != '3.1'):
                raise ValueError('Source occurrence identity differs')
            observations = entry.get('frequency_observations', [])
            state, value = observation_value(observations)
            db.execute('INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL)',
                (key, entry['source_entry_id'], entry['lemma'], entry['grammatical_type'],
                 json.dumps(entry['grammatical_features'], ensure_ascii=False, sort_keys=True),
                 entry['source_status'], value, state, json.dumps(observations, ensure_ascii=False)))
            counts['source_entries'] += 1
            counts[state] += 1
            counts['zero'] += state == 'eligible' and value == 0
            shard_counts[provenance['source_shard']] += 1
            if counts['source_entries'] % 10000 == 0:
                db.commit()
                print(json.dumps({'processed': counts['source_entries'], 'seconds': round(time.monotonic()-started, 2)}), flush=True)
        if counts['source_entries'] != original['entries']:
            raise ValueError('Complete source inventory count differs')
        n, less = counts['eligible'], 0
        bins = Counter()
        for value, equal in db.execute("SELECT frequency,count(*) FROM positions WHERE state='eligible' GROUP BY frequency ORDER BY frequency").fetchall():
            greater = n-less-equal
            q_min = min(4, 1+(4*less)//n)
            q_max = min(4, (4*(less+equal)+n-1)//n)
            db.execute("UPDATE positions SET rank_min=?,rank_max=?,less_count=?,equal_count=?,quartile_min=?,quartile_max=? WHERE state='eligible' AND frequency=?",
                (greater+1, greater+equal, less, equal, q_min, q_max, value))
            label = '0' if value == 0 else str(len(str(value))-1)
            bins[label] += equal
            less += equal
        db.commit()
        if less != n or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Derived frequency inventory differs')
    finally:
        src.close()
        db.close()
    after = database.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('Source database changed during build')
    # Zero has its own bin; positive bins are decimal orders of magnitude.
    histogram = [{'minimum': 0, 'maximum': 0, 'count': counts['zero']}]
    for label, amount in sorted(bins.items(), key=lambda x: int(x[0])):
        if label == '0':
            amount -= counts['zero']
        if amount:
            power = int(label)
            histogram.append({'minimum': 10**power, 'maximum': 10**(power+1)-1, 'count': amount})
    result = {**recipe, 'schema_version': 1, 'resource_id': 'sloleks-frequency-position',
        'artifact': output.name, 'status': 'READY', 'build_id': build_id,
        'source_url': original.get('source_url'), 'license': original.get('license'),
        'built_at': datetime.now(timezone.utc).isoformat(), 'counts': dict(counts),
        'shard_counts': dict(sorted(shard_counts.items())), 'bins': histogram,
        'source_profile_directory': str(source), 'database_sha256': sha(target),
        'population_label': 'Sloleks 3.1 source lexeme occurrences with recorded Gigafida 2.0 head frequency; not all Gigafida words.',
        'elapsed_seconds': round(time.monotonic()-started, 3)}
    (temporary / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    target.chmod(0o444)
    (temporary / 'manifest.json').chmod(0o444)
    temporary.chmod(0o555)
    os.rename(temporary, output)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), ensure_ascii=False, indent=2))
