"""Read-only CJVT synonym release adapter; never parses source XML at runtime."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata

SCHEMA_VERSION = 1
MANIFEST_SCHEMA = 'linghub.synonyms.release.v1'
NORMALIZATION = 'nfkc-casefold-whitespace-v1'


def normalize(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class SynonymIndex:
    def __init__(self, release_dir):
        self.release_dir = Path(release_dir).resolve(strict=True)
        manifest_path = self.release_dir / 'release-manifest.json'
        if manifest_path.is_symlink():
            raise ValueError('Release manifest must be a regular file')
        self.manifest = json.loads(manifest_path.read_text())
        if (self.manifest.get('schema') != MANIFEST_SCHEMA or self.manifest.get('status') != 'VALIDATED'
                or self.manifest.get('normalization') != NORMALIZATION):
            raise ValueError('Unsupported or unvalidated synonym release')
        database = self.manifest['database']
        if database['filename'] != 'synonyms.sqlite' or database['schema_version'] != SCHEMA_VERSION:
            raise ValueError('Unsupported synonym database identity')
        self.database_path = self.release_dir / database['filename']
        if self.database_path.is_symlink() or file_sha256(self.database_path) != database['sha256']:
            raise ValueError('Synonym database checksum mismatch')
        if self.database_path.stat().st_size != database['bytes']:
            raise ValueError('Synonym database size mismatch')
        for filename, key in [('qa-summary.json', 'qa_sha256'), ('THIRD_PARTY_NOTICES.md', 'notice_sha256')]:
            path = self.release_dir / filename
            if path.is_symlink() or file_sha256(path) != self.manifest[key]:
                raise ValueError('Synonym release evidence checksum mismatch: ' + filename)
        self.qa = json.loads((self.release_dir / 'qa-summary.json').read_text())
        if self.qa.get('status') != 'PASS' or not self.qa.get('source_rows_equal_database_rows'):
            raise ValueError('Synonym compilation validation did not pass')
        with self._connect() as db:
            metadata = {row['key']: json.loads(row['value']) for row in db.execute('SELECT key,value FROM metadata')}
            for key in ('schema_version', 'build_id', 'fingerprint', 'source_xml_sha256'):
                expected = SCHEMA_VERSION if key == 'schema_version' else (
                    self.manifest['source']['xml_sha256'] if key == 'source_xml_sha256' else self.manifest[key])
                if metadata.get(key) != expected:
                    raise ValueError('Synonym database metadata mismatch: ' + key)
            for table, count in [('entries', 'entries'), ('lexical_units', 'lexical_units'),
                                 ('senses', 'senses'), ('relations', 'relation_occurrences')]:
                if db.execute('SELECT count(*) FROM ' + table).fetchone()[0] != self.manifest['counts'][count]:
                    raise ValueError('Synonym row accounting mismatch: ' + table)

    @contextmanager
    def _connect(self):
        # A short-lived connection belongs to the calling thread and never writes
        # a journal or WAL. The underlying release is immutable after validation.
        db = sqlite3.connect(self.database_path.as_uri() + '?mode=ro&immutable=1', uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        try:
            yield db
        finally:
            db.close()

    def readiness(self) -> dict:
        return {'status': 'READY', 'available': True, 'resource': 'synonyms',
                'build_id': self.manifest['build_id'], 'fingerprint': self.manifest['fingerprint'],
                'schema_version': SCHEMA_VERSION, 'counts': dict(self.manifest['counts']),
                'provenance_kind': self.manifest['source']['provenance_kind'],
                'source_xml_sha256': self.manifest['source']['xml_sha256'],
                'database_sha256': self.manifest['database']['sha256'], 'read_only': True,
                'source_caveats': self.manifest['source_caveats']}

    def lookup_evidence(self, lemma: str, *, normalized: bool = False) -> dict:
        """Return ordered source evidence, including unresolved references.

        Document positions are named *_order. They are not invented source IDs.
        Exact lookup is the default; normalized lookup is explicitly additive.
        """
        if not isinstance(lemma, str) or len(lemma) > 1000:
            raise ValueError('Lemma must be text of at most 1000 characters')
        lemma = lemma.strip()
        key = normalize(lemma) if normalized else lemma
        column = 'lookup_key' if normalized else 'headword'
        entries = []
        with self._connect() as db:
            for entry in db.execute('SELECT * FROM entries WHERE ' + column + '=? ORDER BY entry_order', (key,)):
                units = [{'lexical_unit_id': row['id'], 'unit_order': row['unit_order'],
                          'source_attributes': json.loads(row['attributes_json']),
                          'content': json.loads(row['content_json'])}
                         for row in db.execute('SELECT * FROM lexical_units WHERE entry_order=? ORDER BY unit_order',
                                               (entry['entry_order'],))]
                senses = []
                for sense in db.execute('SELECT * FROM senses WHERE entry_order=? ORDER BY sense_order',
                                        (entry['entry_order'],)):
                    relations = []
                    query = '''SELECT r.*,te.headword AS target_headword,ts.id AS resolved_sense_id,
                                      tl.id AS resolved_lexical_unit_id,ts.entry_order AS target_sense_entry,
                                      tl.entry_order AS target_lexical_unit_entry
                               FROM relations r LEFT JOIN lexical_units tl ON tl.id=r.target_lexical_unit_id
                               LEFT JOIN entries te ON te.entry_order=tl.entry_order
                               LEFT JOIN senses ts ON ts.id=r.target_sense_id
                               WHERE r.source_sense_id=? ORDER BY r.relation_order'''
                    for row in db.execute(query, (sense['id'],)):
                        relations.append({'source_order': row['relation_order'],
                            'target_lexical_unit_id': row['target_lexical_unit_id'],
                            'target_sense_id': row['target_sense_id'], 'relation_type': row['relation_type'],
                            'synonym_type': row['synonym_type'], 'synonym_score': row['synonym_score'],
                            'raw_text': row['raw_text'], 'target_headword': row['target_headword'],
                            'target_lexical_unit_resolved': row['resolved_lexical_unit_id'] is not None,
                            'target_sense_resolved': row['resolved_sense_id'] is not None,
                            'target_references_consistent': (row['target_sense_entry'] == row['target_lexical_unit_entry']
                                if row['target_sense_entry'] is not None and row['target_lexical_unit_entry'] is not None else None),
                            'source_attributes': json.loads(row['attributes_json'])})
                    senses.append({'sense_id': sense['id'], 'sense_order': sense['sense_order'],
                                   'source_attributes': json.loads(sense['attributes_json']),
                                   'metadata': json.loads(sense['metadata_json']), 'relations': relations})
                entries.append({'entry_order': entry['entry_order'], 'source_entry_id': entry['source_entry_id'],
                                'headword': entry['headword'], 'head_status': entry['head_status'],
                                'source_attributes': json.loads(entry['attributes_json']),
                                'head_metadata': json.loads(entry['head_metadata_json']),
                                'lexical_units': units, 'senses': senses})
        source = self.manifest['source']
        return {'lemma': lemma, 'lookup_mode': 'normalized' if normalized else 'exact', 'lookup_key': key,
                'build_id': self.manifest['build_id'], 'fingerprint': self.manifest['fingerprint'],
                'resource': {key: source[key] for key in ('resource_id', 'title', 'version', 'publisher', 'handle',
                                                        'landing_url', 'license', 'provenance_kind', 'xml_sha256')},
                'entries': entries, 'count_entries': len(entries),
                'count_senses': sum(len(entry['senses']) for entry in entries),
                'count_relations': sum(len(sense['relations']) for entry in entries for sense in entry['senses']),
                'ordering': 'source entry, sense and relation order; directed relations are never expanded'}

    def get_senses_for_lemma(self, lemma: str) -> list[dict]:
        """Legacy projection: exact lemma, original grouping and string targets.

        Missing synonymType uses the old parser's core default only here. The
        evidence API and database preserve NULL; it is not source core evidence.
        The existing router remains responsible for sorting/deduplicating strings.
        """
        output = []
        for entry in self.lookup_evidence(lemma)['entries']:
            for sense in entry['senses']:
                projected = {'sense_id': sense['sense_id'], 'core': [], 'near': [], 'antonyms': []}
                for relation in sense['relations']:
                    target = relation['target_headword'] or relation['raw_text'].strip()
                    if not target:
                        continue
                    if relation['relation_type'] == 'synonym':
                        group = 'core' if relation['synonym_type'] in (None, 'core') else 'near'
                        projected[group].append(target)
                    elif relation['relation_type'] == 'antonym':
                        projected['antonyms'].append(target)
                if any(projected[key] for key in ('core', 'near', 'antonyms')):
                    output.append(projected)
        return output
