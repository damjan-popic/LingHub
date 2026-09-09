"""Bounded, read-only queries over an explicitly compiled CJVT KSSS release."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

SCHEMA_VERSION = 1
NORMALIZATION = 'strip-casefold-v1'


def normalize(value: str) -> str:
    return value.strip().casefold()


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class CompiledCollocations:
    """No source XML parsing, network access, or whole-dictionary RAM index."""
    def __init__(self, release_dir: str | Path):
        self.release_dir = Path(release_dir).resolve(strict=True)
        self.database = self.release_dir / 'collocations.sqlite'
        self.manifest = json.loads((self.release_dir / 'release-manifest.json').read_text())
        m = self.manifest
        if (m.get('status') != 'READY_CANDIDATE' or m.get('schema_version') != SCHEMA_VERSION
                or m.get('normalization') != NORMALIZATION or m.get('resource_kind') != 'collocations'
                or self.database.is_symlink() or not self.database.is_file()
                or sha256(self.database) != m.get('database_sha256')):
            raise ValueError('Compiled collocation release identity is invalid')
        with closing(self.connect()) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
                raise ValueError('Unsupported collocation SQLite schema')
            if db.execute("SELECT value FROM metadata WHERE key='build_id'").fetchone()[0] != m['build_id']:
                raise ValueError('Database and manifest build identities differ')

    def connect(self):
        db = sqlite3.connect(self.database.as_uri() + '?mode=ro&immutable=1', uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA cache_size=-4096')
        return db

    def readiness(self) -> dict:
        return {'status': 'READY_CANDIDATE', 'usable': True, 'resource_kind': 'collocations',
                'build_id': self.manifest['build_id'], 'database_sha256': self.manifest['database_sha256'],
                'counts': self.manifest['counts'], 'source': self.manifest['source'],
                'read_only': True, 'raw_xml_loaded': False, 'operational_activation': False}

    def get_collocations_for_lemma(self, lemma: str, *, min_freq: int | None = 1,
            min_logdice: float | None = None, structure_id: int | None = None,
            status: str | None = None, limit: int = 50, sort: str = 'freq', order: str = 'desc') -> dict:
        if sort not in ('freq', 'logdice') or order not in ('asc', 'desc'):
            raise ValueError('Invalid collocation ordering')
        key = normalize(lemma)
        limit = min(500, max(1, int(limit)))
        clauses, params = ['o.headword_key=?', "o.kind='collocation'"], [key]
        effective_frequency = None if min_freq is None else max(1, int(min_freq))
        if effective_frequency is not None:
            clauses.append('o.frequency>=?')
            params.append(effective_frequency)
        if min_logdice is not None:
            clauses.append('o.logdice>=?')
            params.append(float(min_logdice))
        if structure_id is not None:
            clauses.append('o.structure_id=?')
            params.append(int(structure_id))
        if status is not None:
            clauses.append('o.status_key=?')
            params.append(status.strip().casefold())
        direction = order.upper()
        first, second = ('o.frequency_sort', 'o.logdice_sort') if sort == 'freq' else ('o.logdice_sort', 'o.frequency_sort')
        query = '''SELECT o.*, e.headword, e.headword_lu_id, e.source_entry_id,
                          e.source_ordinal AS entry_ordinal, s.source_sense_id,
                          s.source_ordinal AS sense_ordinal, sh.name AS source_shard, sh.sha256 AS shard_sha256
                   FROM occurrences o JOIN entries e ON e.id=o.entry_id JOIN senses s ON s.id=o.sense_id
                   JOIN shards sh ON sh.id=e.shard_id WHERE ''' + ' AND '.join(clauses)
        query += f' ORDER BY {first} {direction}, {second} {direction}, o.surface {direction}, o.id ASC LIMIT ?'
        with closing(self.connect()) as db:
            total = db.execute("SELECT count(*) FROM occurrences WHERE headword_key=? AND kind='collocation'", (key,)).fetchone()[0]
            head = db.execute('SELECT headword FROM entries WHERE headword_key=? ORDER BY id LIMIT 1', (key,)).fetchone()
            rows = db.execute(query, [*params, limit]).fetchall()
            components = defaultdict(list)
            if rows:
                placeholders = ','.join('?' for _ in rows)
                for c in db.execute('SELECT * FROM components WHERE occurrence_id IN (' + placeholders + ') ORDER BY occurrence_id,position', [r['id'] for r in rows]):
                    raw = json.loads(c['attributes_json'])
                    inferred = 'headword' if c['lemma'] and normalize(c['lemma']) == key else 'collocate' if c['lemma'] else 'other'
                    components[c['occurrence_id']].append({'position': c['position'], 'num': c['num'],
                        'text': c['surface'], 'lemma': c['lemma'], 'msd': c['msd'],
                        'role': c['source_role'] if c['source_role'] is not None else inferred,
                        'role_origin': 'source' if c['source_role'] is not None else 'lemma_comparison',
                        'source_text': c['source_text'], 'source_tail': c['source_tail'], 'source_attributes': raw})
        records = []
        for r in rows:
            records.append({'headword': r['headword'], 'headword_key': r['headword_key'],
                'sense_id': r['source_sense_id'], 'lexical_unit_id': r['lexical_unit_id'],
                'collocation_id': r['source_collocation_id'], 'structure_id': r['structure_id'],
                'status': r['status'], 'frequency': r['frequency'], 'logDice': r['logdice'],
                'surface': r['surface'], 'components': components[r['id']],
                'occurrence_id': r['id'], 'headword_lexical_unit_id': r['headword_lu_id'],
                'source_attributes': json.loads(r['attributes_json']),
                'provenance': {'kind': 'official_release_acquisition', 'handle': self.manifest['source']['handle'],
                    'archive_sha256': self.manifest['source']['archive_sha256'], 'source_shard': r['source_shard'],
                    'shard_sha256': r['shard_sha256'], 'source_entry_id': r['source_entry_id'],
                    'entry_ordinal': r['entry_ordinal'], 'sense_ordinal': r['sense_ordinal'],
                    'example_ordinal': r['source_ordinal'], 'build_id': self.manifest['build_id']}})
        return {'lemma': head[0] if head else lemma.strip(), 'lemma_key': key,
                'count_total': total, 'count_returned': len(records), 'filters': {
                    'min_freq': effective_frequency, 'min_logdice': min_logdice, 'structure_id': structure_id,
                    'status': status, 'limit': limit, 'sort': sort, 'order': order},
                'collocations': records, 'resource': {'build_id': self.manifest['build_id'],
                    'frequency_null_policy': 'Excluded by numeric min_freq; min_freq=null retains missing values',
                    'component_order': 'XML document order; @num preserved separately', 'deduplicated': False}}

    lookup = get_collocations_for_lemma
