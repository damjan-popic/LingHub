"""Presentation-only positions in a pinned, complete source-entry inventory."""
from __future__ import annotations

import copy
from contextlib import closing
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3

from core.lexical_identity import source_occurrence_key

VERSION = 'sloleks-lexeme-frequency-position-v1'
CORPUS = 'Gigafida 2.0'


def observation_value(observations):
    """One source unit/value; never add readings, homographs or corpora."""
    selected = [row for row in observations if isinstance(row, dict)
        and (row.get('measure'), row.get('corpus'), row.get('scope'), row.get('unit'))
            == ('frequency', CORPUS, 'lexeme', 'occurrences')]
    if not selected:
        return 'missing', None
    if any(type(row.get('value')) is not int or row['value'] < 0 for row in selected):
        return 'invalid', None
    values = {row['value'] for row in selected}
    if len(values) != 1:
        return 'conflicting', None
    return 'eligible', values.pop()


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Reference:
    """Validate once on startup; requests query this small immutable sidecar."""
    def __init__(self, manifest_path):
        self.manifest_path = Path(manifest_path).resolve(strict=True)
        self.directory = self.manifest_path.parent
        self.database = self.directory / 'frequency-position.sqlite'
        self.manifest = json.loads(self.manifest_path.read_text())
        m = self.manifest
        if (m.get('version') != VERSION or m.get('status') != 'READY'
                or m.get('corpus') != CORPUS or m.get('scope') != 'lexeme'
                or m.get('population_unit') != 'source_lexeme_occurrence'
                or self.database.is_symlink() or not self.database.is_file()
                or _sha(self.database) != m.get('database_sha256')):
            raise ValueError('Frequency position reference identity differs')
        self.identity = self._identity()
        self.bins = copy.deepcopy(m.get('bins', []))
        with closing(self.connect()) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError('Unknown frequency position schema')
            identity = db.execute('SELECT value FROM metadata WHERE key=?', ('build_id',)).fetchone()
            if identity is None or identity[0] != m.get('build_id'):
                raise ValueError('Frequency position build differs')

    def _identity(self):
        s = self.database.stat()
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    def connect(self):
        if self._identity() != self.identity:
            raise ValueError('Frequency position reference changed')
        db = sqlite3.connect(self.database.as_uri() + '?mode=ro&immutable=1', uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return db

    @lru_cache(maxsize=512)
    def _lookup(self, key):
        with closing(self.connect()) as db:
            row = db.execute('SELECT * FROM positions WHERE occurrence_key=?', (key,)).fetchone()
        return dict(row) if row else None

    def lookup(self, key):
        # Check file identity even when the immutable row is already cached.
        if self._identity() != self.identity:
            raise ValueError('Frequency position reference changed')
        return copy.deepcopy(self._lookup(key))


def annotate(view, bundle, reference):
    """Add matched row metadata; frozen source/capture/answer bytes stay intact."""
    result = copy.deepcopy(view)
    if reference is None or not isinstance(result, dict):
        return result
    from .lexical import profile
    captured = profile(bundle)
    if not captured:
        return result
    manifest = reference.manifest
    matched = {}
    for part in captured.get('facets', {}).get('morphology', {}).get('data') or []:
        data = part.get('result') or {}
        inventory = data.get('provenance') or {}
        if (inventory.get('database_sha256') != manifest['source_database_sha256']
                or inventory.get('source_archive_sha256') != manifest['source_archive_sha256']):
            continue
        for lexeme in data.get('lexemes', []):
            provenance = lexeme.get('evidence') or {}
            if (provenance.get('resource_id'), provenance.get('version'), provenance.get('source_archive_sha256')) != (
                    'sloleks', '3.1', manifest['source_archive_sha256']):
                continue
            key = source_occurrence_key(provenance)
            state, frequency = observation_value(lexeme.get('frequency_observations', []))
            if not key or state != 'eligible':
                continue
            try:
                row = reference.lookup(key)
            except (OSError, ValueError, sqlite3.Error):
                return result
            if (not row or row['state'] != 'eligible' or row['frequency'] != frequency
                    or row['source_entry_id'] != lexeme.get('source_entry_id')
                    or row['lemma'] != lexeme.get('lemma')
                    or row['grammatical_type'] != lexeme.get('grammatical_type')
                    or json.loads(row['grammar_json']) != lexeme.get('grammatical_features')
                    or row['source_status'] != lexeme.get('source_status')):
                continue
            n = manifest['counts']['eligible']
            low = 100 * row['less_count'] / n
            high = 100 * (row['less_count'] + row['equal_count']) / n
            matched[key] = (row['source_entry_id'], {'version': VERSION, 'corpus': CORPUS, 'inventory': 'Sloleks 3.1',
                'population_unit': 'source_lexeme_occurrence', 'frequency': frequency,
                'eligible_total': n, 'rank_min': row['rank_min'], 'rank_max': row['rank_max'],
                'percentile_low': low, 'percentile_high': high, 'percentile_mid': (low + high) / 2,
                'quartile_min': row['quartile_min'], 'quartile_max': row['quartile_max'],
                'build_id': manifest['build_id']})

    def visit(value):
        if isinstance(value, dict):
            key = value.get('source_occurrence_key')
            if key in matched:
                entry_id, position = matched[key]
                if value.get('record_id') == entry_id:
                    value['frequency_position'] = copy.deepcopy(position)
            for child in list(value.values()):
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(result)
    return result
