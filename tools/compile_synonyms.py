#!/usr/bin/env python3
"""Compile verified CJVT thesaurus 2.2 XML into a new private SQLite candidate."""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.synonym_index import MANIFEST_SCHEMA, NORMALIZATION, SCHEMA_VERSION, file_sha256, normalize
from tools.xml_safety import safe_iterparse

CONVERTER_VERSION = 'cjvt-thesaurus-2.2-v1'
DDL = '''
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE entries (
 entry_order INTEGER PRIMARY KEY, source_entry_id TEXT, headword TEXT NOT NULL, lookup_key TEXT NOT NULL,
 head_status TEXT, attributes_json TEXT NOT NULL, head_metadata_json TEXT NOT NULL);
CREATE TABLE lexical_units (
 id TEXT PRIMARY KEY, entry_order INTEGER NOT NULL REFERENCES entries(entry_order), unit_order INTEGER NOT NULL,
 attributes_json TEXT NOT NULL, content_json TEXT NOT NULL, UNIQUE(entry_order,unit_order));
CREATE TABLE senses (
 id TEXT PRIMARY KEY, entry_order INTEGER NOT NULL REFERENCES entries(entry_order), sense_order INTEGER NOT NULL,
 attributes_json TEXT NOT NULL, metadata_json TEXT NOT NULL, UNIQUE(entry_order,sense_order));
CREATE TABLE relations (
 source_sense_id TEXT NOT NULL REFERENCES senses(id), relation_order INTEGER NOT NULL,
 target_lexical_unit_id TEXT, target_sense_id TEXT, relation_type TEXT NOT NULL,
 synonym_type TEXT, synonym_score TEXT, raw_text TEXT NOT NULL, attributes_json TEXT NOT NULL,
 PRIMARY KEY(source_sense_id,relation_order));
'''


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def xml_record(element) -> dict:
    return {'tag': element.tag, 'attributes': dict(element.attrib), 'text': element.text,
            'tail': element.tail, 'children': [xml_record(child) for child in element]}


def one(element, path, *, required=True):
    found = element.findall(path)
    require(len(found) == 1 if required else len(found) <= 1, 'Unexpected cardinality at ' + path)
    return found[0] if found else None


def source_identity(xml_path: Path, provenance: dict) -> dict:
    require(provenance.get('schema') == 'lingstack.official-resource-acquisition.v1' and
            provenance.get('status') == 'VERIFIED' and
            provenance.get('provenance_kind') == 'official_release_acquisition',
            'A verified official acquisition manifest is required')
    raw = Path(provenance['raw_location']).resolve(strict=True)
    relative = xml_path.resolve(strict=True).relative_to(raw / 'source').as_posix()
    members = [item for item in provenance['archive_members'] if item['path'] == relative and not item['directory']]
    require(len(members) == 1, 'XML must have one exact verified archive member identity')
    member = members[0]
    require(xml_path.stat().st_size == member['bytes'] and file_sha256(xml_path) == member['sha256'],
            'XML bytes differ from verified acquisition')
    result = {key: provenance[key] for key in ('resource_id', 'title', 'version', 'publisher', 'handle',
                                             'landing_url', 'license', 'provenance_kind')}
    result.update(xml_filename=xml_path.name, xml_member=relative, xml_sha256=member['sha256'],
                  xml_bytes=member['bytes'], archive_sha256=provenance['sha256'],
                  acquisition_manifest_sha256=hashlib.sha256(canonical(provenance).encode()).hexdigest())
    return result


def insert_entry(db, entry, ordinal: int, counts: Counter) -> None:
    require(entry.tag == 'entry', 'Unexpected dictionary child')
    require(all(child.tag in ('head', 'body') for child in entry), 'Unsupported entry child')
    head, body = one(entry, 'head'), one(entry, 'body')
    lemma = one(one(head, 'headword'), 'lemma')
    require(not list(lemma) and lemma.text is not None and lemma.text.strip(), 'Missing or structured headword')
    headword = lemma.text.strip()
    units = head.findall('lexicalUnit')
    require(len(units) == 1, 'CJVT 2.2 profile requires one lexical unit per entry')
    sense_list = one(body, 'senseList')
    require(all(child.tag == 'sense' for child in sense_list), 'Unsupported sense list child')
    require(all(child.tag == 'senseList' for child in body), 'Unsupported body child')
    status_element = one(head, 'status', required=False)
    head_status = status_element.text if status_element is not None else None
    db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?,?)',
               (ordinal, entry.get('id'), headword, normalize(headword), head_status, canonical(dict(entry.attrib)),
                canonical([xml_record(child) for child in head if child.tag != 'lexicalUnit'])))
    counts['entries'] += 1
    counts['head_status:' + str(head_status)] += 1
    for unit_order, unit in enumerate(units, 1):
        unit_id = unit.get('id')
        require(unit_id is not None and unit_id.strip(), 'Missing source lexical-unit ID')
        db.execute('INSERT INTO lexical_units VALUES (?,?,?,?,?)',
                   (unit_id, ordinal, unit_order, canonical(dict(unit.attrib)), canonical(xml_record(unit))))
        counts['lexical_units'] += 1
    for sense_order, sense in enumerate(sense_list, 1):
        sense_id = sense.get('id')
        require(sense_id is not None and sense_id.strip(), 'Missing source sense ID')
        db.execute('INSERT INTO senses VALUES (?,?,?,?,?)',
                   (sense_id, ordinal, sense_order, canonical(dict(sense.attrib)),
                    canonical([xml_record(child) for child in sense if child.tag != 'relatedSenseList'])))
        counts['senses'] += 1
        if sense.get('dummy') is not None:
            require(sense.get('dummy') in ('true', 'false', '0', '1'), 'Invalid dummy sense flag')
            counts['dummy_senses'] += sense.get('dummy') in ('true', '1')
        relation_list = one(sense, 'relatedSenseList', required=False)
        if relation_list is None:
            counts['senses_without_relation_list'] += 1
            continue
        for relation_order, relation in enumerate(relation_list, 1):
            require(relation.tag == 'relatedSense' and not list(relation), 'Unsupported related sense structure')
            relation_type = relation.get('type')
            require(relation_type in ('synonym', 'antonym'), 'Unsupported source relation type')
            synonym_type = relation.get('synonymType')
            require(synonym_type in (None, 'core', 'near'), 'Unsupported source synonym grouping')
            score = relation.get('synonymScore')
            if score is not None:
                try:
                    require(Decimal(score).is_finite(), 'Nonfinite synonym score')
                except InvalidOperation:
                    raise ValueError('Malformed synonym score') from None
            raw_text = relation.text or ''
            target_lu, target_sense = relation.get('lexical_unit_id'), relation.get('sense_id')
            require(target_lu or target_sense or raw_text.strip(), 'Relation has no source target evidence')
            db.execute('INSERT INTO relations VALUES (?,?,?,?,?,?,?,?,?)',
                       (sense_id, relation_order, target_lu, target_sense, relation_type, synonym_type,
                        score, raw_text, canonical(dict(relation.attrib))))
            counts['relation_occurrences'] += 1
            counts[relation_type + '_occurrences'] += 1
            counts['group:' + relation_type + ':' + str(synonym_type)] += 1


def build(xml_path, output_dir, provenance: dict) -> dict:
    """Create a new candidate; refuse existing output and unverifiable input."""
    started = time.monotonic()
    xml_path, output_dir = Path(xml_path), Path(output_dir)
    source = source_identity(xml_path, provenance)
    require(not output_dir.exists() and not output_dir.is_symlink(), 'Candidate output must not already exist')
    notice = Path(provenance['notice_location'])
    require(not notice.is_symlink() and notice.is_file(), 'Verified resource notice is required')
    compiler_root = Path(__file__).resolve().parents[1]
    code_hashes = {str(path.relative_to(compiler_root)): file_sha256(path)
                   for path in (Path(__file__).resolve(), compiler_root / 'core/synonym_index.py',
                                compiler_root / 'tools/xml_safety.py')}
    identity = {'converter': CONVERTER_VERSION, 'converter_files': code_hashes, 'source': source,
                'schema_version': SCHEMA_VERSION, 'schema_sha256': hashlib.sha256(DDL.encode()).hexdigest(),
                'normalization': NORMALIZATION, 'notice_sha256': file_sha256(notice)}
    fingerprint = hashlib.sha256(canonical(identity).encode()).hexdigest()
    build_id = 'cjvt-synonyms-v1-' + fingerprint[:24]
    old_mask = os.umask(0o077)
    try:
        output_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_dir.mkdir(mode=0o700)
    finally:
        os.umask(old_mask)
    database_path = output_dir / 'synonyms.sqlite'
    db = sqlite3.connect(database_path)
    counts = Counter({key: 0 for key in ('entries', 'lexical_units', 'senses', 'relation_occurrences',
                                        'synonym_occurrences', 'antonym_occurrences', 'dummy_senses')})
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA temp_store=FILE')
        db.execute('PRAGMA cache_size=-8192')
        db.executescript(DDL)
        with db:
            root = None
            for event, element in safe_iterparse(xml_path, events=('start', 'end')):
                if root is None:
                    root = element
                    require(root.tag == 'dictionary' and not root.attrib, 'Unsupported dictionary root profile')
                if event == 'end' and element.tag == 'entry':
                    insert_entry(db, element, counts['entries'] + 1, counts)
                    root.remove(element)
                    element.clear()
            require(root is not None and counts['entries'] > 0 and not list(root), 'Empty or unsupported dictionary content')
            require(file_sha256(xml_path) == source['xml_sha256'], 'XML changed during compilation')
            for key, value in {'schema_version': SCHEMA_VERSION, 'build_id': build_id,
                               'fingerprint': fingerprint, 'source_xml_sha256': source['xml_sha256']}.items():
                db.execute('INSERT INTO metadata VALUES (?,?)', (key, canonical(value)))
        db.executescript('''CREATE INDEX idx_entries_headword ON entries(headword,entry_order);
            CREATE INDEX idx_entries_lookup ON entries(lookup_key,entry_order);
            CREATE INDEX idx_lexical_units_entry ON lexical_units(entry_order,unit_order);
            CREATE INDEX idx_senses_entry ON senses(entry_order,sense_order);
            CREATE INDEX idx_relations_target_lu ON relations(target_lexical_unit_id);
            CREATE INDEX idx_relations_target_sense ON relations(target_sense_id);''')
        row_counts = {key: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                      for table, key in [('entries', 'entries'), ('lexical_units', 'lexical_units'),
                                         ('senses', 'senses'), ('relations', 'relation_occurrences')]}
        require(all(counts[key] == value for key, value in row_counts.items()), 'Parser/database row counts differ')
        require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'SQLite integrity check failed')
        require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'Source-side foreign keys failed')
        reference_diagnostics = {}
        for label, target, table in [('lexical_unit', 'target_lexical_unit_id', 'lexical_units'),
                                      ('sense', 'target_sense_id', 'senses')]:
            rows = db.execute('SELECT r.' + target + ', count(*) FROM relations r LEFT JOIN ' + table +
                              ' t ON t.id=r.' + target + ' WHERE r.' + target + ' IS NOT NULL AND t.id IS NULL GROUP BY r.' + target +
                              ' ORDER BY r.' + target).fetchall()
            reference_diagnostics[label] = {'unresolved_unique_ids': len(rows),
                'unresolved_occurrences': sum(row[1] for row in rows),
                'ids_and_occurrences': [{'source_id': row[0], 'occurrences': row[1]} for row in rows]}
        reference_diagnostics['target_pair_disagreements'] = db.execute('''SELECT count(*) FROM relations r
            JOIN lexical_units l ON l.id=r.target_lexical_unit_id JOIN senses s ON s.id=r.target_sense_id
            WHERE l.entry_order<>s.entry_order''').fetchone()[0]
        counts['duplicate_headword_strings'] = db.execute('SELECT count(*) FROM (SELECT headword FROM entries GROUP BY headword HAVING count(*)>1)').fetchone()[0]
        counts['unique_headwords'] = db.execute('SELECT count(DISTINCT headword) FROM entries').fetchone()[0]
        plans = {key: [row[3] for row in db.execute('EXPLAIN QUERY PLAN SELECT entry_order FROM entries WHERE ' + column + '=?', ('otrok',))]
                 for key, column in [('exact', 'headword'), ('normalized', 'lookup_key')]}
        require(all(any('SEARCH' in line and 'INDEX' in line for line in plan) for plan in plans.values()),
                'Headword lookup does not use an index')
        db.execute('ANALYZE')
        db.commit()
    finally:
        db.close()
    shutil.copyfile(notice, output_dir / 'THIRD_PARTY_NOTICES.md')
    references = provenance.get('catalogue_reference_counts', {})
    differences = {key: {'catalogue': expected, 'parsed': counts['synonym_occurrences' if key == 'synonyms' else key],
                         'difference': counts['synonym_occurrences' if key == 'synonyms' else key] - expected}
                   for key, expected in references.items() if key in ('entries', 'synonyms')}
    qa = {'status': 'PASS', 'schema_validation': 'SOURCE_PROFILE_VALIDATED; full XSD validation recorded separately',
          'integrity_check': 'ok', 'foreign_key_check': [], 'source_rows_equal_database_rows': True,
          'parser_counts': dict(counts), 'reference_diagnostics': reference_diagnostics,
          'catalogue_comparison': differences, 'query_plans': plans, 'malformed_records': 0,
          'silently_skipped_records': 0, 'deduplicated_source_occurrences': 0,
          'unresolved_reference_policy': 'Preserve original IDs and text; no invented targets or dropped relations',
          'legacy_projection': 'Exact case-sensitive headword; missing synonymType defaults to core only in legacy projection; router sorts/deduplicates display strings',
          'source_identity_policy': 'Source LU/sense IDs retained as strings; entry/relation positions are explicit document order, not source IDs'}
    (output_dir / 'qa-summary.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2) + '\n')
    manifest = {'schema': MANIFEST_SCHEMA, 'status': 'VALIDATED', 'resource_kind': 'synonyms',
                'build_id': build_id, 'fingerprint': fingerprint, 'normalization': NORMALIZATION,
                'converter': identity, 'source': source, 'counts': dict(counts),
                'database': {'filename': database_path.name, 'schema_version': SCHEMA_VERSION,
                             'sha256': file_sha256(database_path), 'bytes': database_path.stat().st_size},
                'qa_sha256': file_sha256(output_dir / 'qa-summary.json'),
                'notice_sha256': file_sha256(output_dir / 'THIRD_PARTY_NOTICES.md'),
                'source_caveats': {'catalogue_comparison': differences,
                    'unresolved_references': {key: {name: value[name] for name in ('unresolved_unique_ids', 'unresolved_occurrences')}
                                             for key, value in reference_diagnostics.items() if isinstance(value, dict)},
                    'target_pair_disagreements': reference_diagnostics['target_pair_disagreements'],
                    'head_status_is_source_metadata_not_inferred_review': True,
                    'missing_synonym_group_remains_null_in_evidence': True,
                    'legacy_missing_group_default': 'core, compatibility projection only'},
                'runtime_activation': 'NOT_ACTIVATED', 'digitalocean_parity': 'PENDING',
                'compile_seconds': round(time.monotonic() - started, 3)}
    (output_dir / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    files = ['synonyms.sqlite', 'release-manifest.json', 'qa-summary.json', 'THIRD_PARTY_NOTICES.md']
    (output_dir / 'checksums.sha256').write_text(''.join(file_sha256(output_dir / name) + '  ' + name + '\n' for name in files))
    for path in output_dir.iterdir():
        path.chmod(0o600)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xml', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--provenance', required=True, type=Path)
    args = parser.parse_args()
    result = build(args.xml, args.output, json.loads(args.provenance.read_text()))
    print(json.dumps({'status': result['status'], 'build_id': result['build_id'], 'fingerprint': result['fingerprint'],
                      'counts': result['counts'], 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(main())
