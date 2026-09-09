#!/usr/bin/env python3
"""Stream verified CJVT KSSS 2.2 XML into a new private SQLite candidate."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import shutil
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.xml_safety import safe_iterparse
from core.compiled_collocations import NORMALIZATION, SCHEMA_VERSION, CompiledCollocations, normalize, sha256

CONVERTER_VERSION = 'cjvt-ksss-occurrences-v1'
BATCH_SIZE = 4000
SCHEMA = '''
PRAGMA user_version=1;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE shards(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,sha256 TEXT NOT NULL,bytes INTEGER NOT NULL);
CREATE TABLE entries(id INTEGER PRIMARY KEY,shard_id INTEGER NOT NULL REFERENCES shards(id),source_ordinal INTEGER NOT NULL,
 source_entry_id TEXT,headword TEXT NOT NULL,headword_key TEXT NOT NULL,headword_lu_id TEXT,headword_status TEXT,
 attributes_json TEXT NOT NULL,head_xml TEXT NOT NULL,UNIQUE(shard_id,source_ordinal));
CREATE TABLE senses(id INTEGER PRIMARY KEY,entry_id INTEGER NOT NULL REFERENCES entries(id),source_ordinal INTEGER NOT NULL,
 source_sense_id TEXT,attributes_json TEXT NOT NULL,metadata_xml TEXT,UNIQUE(entry_id,source_ordinal));
CREATE TABLE occurrences(id INTEGER PRIMARY KEY,entry_id INTEGER NOT NULL REFERENCES entries(id),
 sense_id INTEGER NOT NULL REFERENCES senses(id),source_ordinal INTEGER NOT NULL,source_collocation_id TEXT,
 lexical_unit_id INTEGER,structure_id INTEGER,kind TEXT NOT NULL,status TEXT,status_key TEXT,frequency INTEGER,logdice REAL,
 headword_key TEXT NOT NULL,surface TEXT NOT NULL,surface_key TEXT NOT NULL,lemma_sequence TEXT NOT NULL,
 frequency_sort INTEGER NOT NULL,logdice_sort REAL NOT NULL,attributes_json TEXT NOT NULL,
 UNIQUE(sense_id,source_ordinal));
CREATE TABLE components(occurrence_id INTEGER NOT NULL REFERENCES occurrences(id),position INTEGER NOT NULL,
 num INTEGER,surface TEXT,lemma TEXT,msd TEXT,source_role TEXT,source_text TEXT,source_tail TEXT,attributes_json TEXT NOT NULL,
 PRIMARY KEY(occurrence_id,position)) WITHOUT ROWID;
CREATE TABLE other_examples(id INTEGER PRIMARY KEY,entry_id INTEGER NOT NULL REFERENCES entries(id),
 sense_id INTEGER NOT NULL REFERENCES senses(id),source_ordinal INTEGER NOT NULL,tag TEXT NOT NULL,source_xml TEXT NOT NULL);
'''
INDEXES = '''
CREATE INDEX entries_headword ON entries(headword_key,id);
CREATE INDEX occurrences_frequency ON occurrences(headword_key,kind,frequency_sort DESC,logdice_sort DESC,surface DESC,id);
CREATE INDEX occurrences_logdice ON occurrences(headword_key,kind,logdice_sort DESC,frequency_sort DESC,surface DESC,id);
CREATE INDEX occurrences_lexical_unit ON occurrences(lexical_unit_id);
CREATE INDEX occurrences_source_id ON occurrences(source_collocation_id);
CREATE INDEX occurrences_surface ON occurrences(surface_key,headword_key);
CREATE INDEX occurrences_lemma_sequence ON occurrences(lemma_sequence,headword_key);
CREATE INDEX occurrences_sense ON occurrences(sense_id);
CREATE INDEX senses_entry ON senses(entry_id);
'''


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def integer(value, label):
    if value is None:
        return None
    if not value.strip() or not value.strip().lstrip('+-').isascii() or not value.strip().lstrip('+-').isdigit():
        raise ValueError('Malformed numeric source attribute: ' + label)
    number = int(value)
    if not -(2**63) <= number < 2**63:
        raise ValueError('Out-of-range numeric source attribute: ' + label)
    return number


def decimal(value, label):
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        raise ValueError('Malformed numeric source attribute: ' + label) from None
    if not math.isfinite(number):
        raise ValueError('Non-finite numeric source attribute: ' + label)
    return number


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def source_files(raw: Path):
    acquisition_path = raw / 'acquisition-manifest.json'
    acquisition = json.loads(acquisition_path.read_text())
    if (acquisition.get('status') != 'VERIFIED' or acquisition.get('resource_id') != 'cjvt-ksss-2.2'
            or acquisition.get('published_md5_match') is not True
            or acquisition.get('provenance_kind') != 'official_release_acquisition'):
        raise ValueError('A verified official KSSS acquisition manifest is required')
    members = [m for m in acquisition['archive_members'] if not m['directory'] and m['path'].endswith('.xml')]
    if not members:
        raise ValueError('Verified acquisition has no XML shards')
    files = []
    for member in sorted(members, key=lambda m: m['path']):
        path = raw / 'source' / member['path']
        path.resolve(strict=True).relative_to((raw / 'source').resolve())
        if path.is_symlink() or not path.is_file() or path.stat().st_size != member['bytes'] or sha256(path) != member['sha256']:
            raise ValueError('Source shard does not match verified acquisition')
        files.append((path, member))
    found = {p.resolve() for p in (raw / 'source').rglob('*.xml')}
    if found != {p.resolve() for p, _ in files}:
        raise ValueError('Unaccounted XML files in source release')
    return acquisition, files


def compile_shards(db, files, progress=None):
    """Retain every entry/sense/example; remove completed examples from XML tree."""
    counts = Counter(entries=0, senses=0, multiword_occurrences=0, collocations=0, components=0, other_examples=0)
    kinds, statuses, missing, attributes = Counter(), Counter(), Counter(), Counter()
    shard_counts = []
    pending_occurrences, pending_components = [], []

    def flush():
        if pending_occurrences:
            db.executemany('INSERT INTO occurrences VALUES (' + ','.join('?' for _ in range(19)) + ')', pending_occurrences)
            db.executemany('INSERT INTO components VALUES (' + ','.join('?' for _ in range(10)) + ')', pending_components)
            pending_occurrences.clear()
            pending_components.clear()
            db.commit()

    for shard_number, (path, member) in enumerate(files, 1):
        db.execute('INSERT INTO shards VALUES (?,?,?,?)', (shard_number, member['path'], member['sha256'], member['bytes']))
        before = counts.copy()
        kinds_before, statuses_before = kinds.copy(), statuses.copy()
        stack = []
        entry = None
        sense = None
        entry_ordinal = 0
        for event, element in safe_iterparse(path, events=('start', 'end')):
            if event == 'start':
                stack.append(element)
                if len(stack) == 1 and element.tag != 'dictionary':
                    raise ValueError('Unsupported source root/namespace')
                if element.tag == 'entry':
                    if len(stack) != 2 or entry is not None:
                        raise ValueError('Unexpected entry nesting')
                    counts['entries'] += 1
                    entry_ordinal += 1
                    entry = {'id': counts['entries'], 'ordinal': entry_ordinal, 'attributes': dict(element.attrib), 'head': None, 'senses': 0}
                elif element.tag == 'sense':
                    if entry is None or entry['head'] is None or sense is not None or [x.tag for x in stack[-3:]] != ['body','senseList','sense']:
                        raise ValueError('Unexpected sense nesting or missing head')
                    counts['senses'] += 1
                    entry['senses'] += 1
                    sense = {'id': counts['senses'], 'ordinal': entry['senses'], 'examples': 0}
                    db.execute('INSERT INTO senses VALUES (?,?,?,?,?,NULL)', (sense['id'], entry['id'], sense['ordinal'], element.get('id'), canonical(dict(element.attrib))))
                continue
            parent = stack[-2] if len(stack) > 1 else None
            if element.tag == 'head' and parent is not None and parent.tag == 'entry':
                if entry is None or entry['head'] is not None:
                    raise ValueError('Duplicate/misplaced entry head')
                lemmas = element.findall('./headword/lemma')
                if len(lemmas) != 1 or lemmas[0].text is None or not lemmas[0].text.strip():
                    raise ValueError('Missing/ambiguous headword; no entry skipped')
                headword = lemmas[0].text.strip()
                entry['head'] = headword
                lu = element.find('./lexicalUnit')
                db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)', (entry['id'], shard_number, entry['ordinal'],
                    entry['attributes'].get('id'), headword, normalize(headword), None if lu is None else lu.get('id'),
                    element.findtext('./status'), canonical(entry['attributes']), ET.tostring(element, encoding='unicode')))
                parent.remove(element)
                element.clear()
            elif element.tag in ('multiwordExample', 'corpusExample'):
                if entry is None or sense is None or [x.tag for x in stack[-4:-1]] != ['sense','exampleContainerList','exampleContainer']:
                    raise ValueError('Example outside supported sense hierarchy')
                sense['examples'] += 1
                if element.tag == 'corpusExample':
                    counts['other_examples'] += 1
                    db.execute('INSERT INTO other_examples VALUES (?,?,?,?,?,?)', (counts['other_examples'], entry['id'], sense['id'], sense['examples'], element.tag, ET.tostring(element, encoding='unicode')))
                else:
                    kind = element.get('type')
                    if not kind:
                        raise ValueError('Missing multiword example type')
                    comps = list(element)
                    if not comps or any(c.tag != 'comp' or len(c) for c in comps):
                        raise ValueError('Malformed multiword components; no occurrence skipped')
                    counts['multiword_occurrences'] += 1
                    counts['collocations'] += int(kind == 'collocation')
                    occurrence = counts['multiword_occurrences']
                    kinds[kind] += 1
                    statuses[element.get('status', '<missing>')] += 1
                    for key in element.attrib:
                        attributes[key] += 1
                    for key in ('id','lexical_unit_id','structure_id','status','frequency','logDice'):
                        missing[key] += int(key not in element.attrib)
                    frequency = integer(element.get('frequency'), 'frequency')
                    logdice = decimal(element.get('logDice'), 'logDice')
                    surfaces = [(c.text or '').strip() for c in comps]
                    surface = ' '.join(t for t in surfaces if t)
                    lemmas = [normalize(c.get('lemma')) for c in comps if c.get('lemma')]
                    pending_occurrences.append((occurrence, entry['id'], sense['id'], sense['examples'], element.get('id'),
                        integer(element.get('lexical_unit_id'), 'lexical_unit_id'), integer(element.get('structure_id'), 'structure_id'),
                        kind, element.get('status'), element.get('status', '').casefold() if element.get('status') is not None else None,
                        frequency, logdice, normalize(entry['head']), surface, normalize(surface), ' '.join(lemmas),
                        frequency or 0, logdice or 0.0, canonical(dict(element.attrib))))
                    for position, c in enumerate(comps, 1):
                        counts['components'] += 1
                        pending_components.append((occurrence, position, integer(c.get('num'), 'component.num'),
                            (c.text.strip() if c.text is not None else None), c.get('lemma'), c.get('msd'), c.get('role'),
                            c.text, c.tail, canonical(dict(c.attrib))))
                    if len(pending_occurrences) >= BATCH_SIZE:
                        flush()
                parent.remove(element)
                element.clear()
            elif element.tag == 'exampleContainer':
                # Preserve any ancillary translation metadata independently.
                if len(element) or element.attrib or (element.text and element.text.strip()):
                    counts['other_examples'] += 1
                    db.execute('INSERT INTO other_examples VALUES (?,?,?,?,?,?)', (counts['other_examples'], entry['id'], sense['id'], sense['examples'], 'exampleContainer_metadata', ET.tostring(element, encoding='unicode')))
                parent.remove(element)
                element.clear()
            elif element.tag == 'exampleContainerList':
                parent.remove(element)
                element.clear()
            elif element.tag == 'sense':
                db.execute('UPDATE senses SET metadata_xml=? WHERE id=?', (ET.tostring(element, encoding='unicode'), sense['id']))
                sense = None
                parent.remove(element)
                element.clear()
            elif element.tag == 'entry':
                if entry is None or entry['head'] is None:
                    raise ValueError('Missing entry head; no entry skipped')
                entry = None
                parent.remove(element)
                element.clear()
            stack.pop()
        flush()
        db.commit()
        record = {'source_shard': member['path'], **{k: counts[k] - before[k] for k in counts},
                  'kinds': dict(kinds - kinds_before), 'statuses_all_multiword': dict(statuses - statuses_before)}
        shard_counts.append(record)
        if progress:
            progress({'event': 'shard_compiled', 'shard': shard_number, 'shards': len(files), **record})
    return {'counts': dict(counts), 'kinds': dict(kinds), 'statuses_all_multiword': dict(statuses),
            'missing_attributes_all_multiword': dict(missing), 'attribute_occurrences': dict(attributes),
            'shards': shard_counts, 'deduplicated_occurrences': 0, 'skipped_records': 0}


def build(raw: Path, output: Path):
    started = time.monotonic()
    acquisition, files = source_files(raw)
    if output.exists():
        raise ValueError('Candidate output must be a new directory; existing bytes are retained')
    output.mkdir(mode=0o700)
    subprocess.run(['/usr/bin/setfacl', '-b', '-k', str(output)], check=True)
    output.chmod(0o700)
    converter_hashes = {str(p.relative_to(REPO)): sha256(p) for p in (
        Path(__file__), REPO / 'core/compiled_collocations.py', REPO / 'tools/xml_safety.py')}
    identity = {'schema_version': SCHEMA_VERSION, 'normalization': NORMALIZATION,
                'converter_version': CONVERTER_VERSION, 'converter_files': converter_hashes,
                'archive_sha256': acquisition['sha256'], 'xml_members': [m for _, m in files]}
    build_id = hashlib.sha256(canonical(identity).encode()).hexdigest()
    manifest = {'status': 'BUILDING', 'resource_kind': 'collocations', **identity, 'build_id': build_id,
                'created_at': datetime.now(timezone.utc).isoformat(), 'source': {
                    'title': acquisition['title'], 'version': acquisition['version'], 'publisher': acquisition['publisher'],
                    'handle': acquisition['handle'], 'license': acquisition['license'], 'landing_url': acquisition['landing_url'],
                    'archive_sha256': acquisition['sha256'], 'provenance_kind': acquisition['provenance_kind'],
                    'raw_location': str(raw), 'acquisition_manifest_sha256': sha256(raw / 'acquisition-manifest.json')},
                'activation': 'CANDIDATE_ONLY', 'digitalocean_parity': 'PENDING', 'xsd_validation': 'SEPARATE_REPORT_REQUIRED'}
    write_json(output / 'release-manifest.json', manifest)
    database = output / 'collocations.sqlite'
    try:
        with closing(sqlite3.connect(database)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA cache_size=-32768')
            db.execute('PRAGMA temp_store=FILE')
            db.execute('PRAGMA synchronous=NORMAL')
            db.executescript(SCHEMA)
            db.execute('INSERT INTO metadata VALUES (?,?)', ('build_id', build_id))
            accounting = compile_shards(db, files, lambda p: print(json.dumps(p), flush=True))
            print(json.dumps({'event': 'building_indexes', 'counts': accounting['counts']}), flush=True)
            db.executescript(INDEXES)
            db.execute('ANALYZE')
            counts = accounting['counts']
            for table, label in [('entries','entries'), ('senses','senses'), ('occurrences','multiword_occurrences'), ('components','components'), ('other_examples','other_examples')]:
                if db.execute('SELECT count(*) FROM ' + table).fetchone()[0] != counts[label]:
                    raise ValueError('XML/SQLite row accounting differs')
            if db.execute("SELECT count(*) FROM occurrences WHERE kind='collocation'").fetchone()[0] != counts['collocations']:
                raise ValueError('Collocation row accounting differs')
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('SQLite integrity or relationship check failed')
            counts['distinct_headwords'] = db.execute('SELECT count(DISTINCT headword_key) FROM entries').fetchone()[0]
            counts['entries_with_collocations'] = db.execute("SELECT count(DISTINCT entry_id) FROM occurrences WHERE kind='collocation'").fetchone()[0]
            counts['entries_without_collocations'] = counts['entries'] - counts['entries_with_collocations']
            counts['entries_with_multiword_examples'] = db.execute('SELECT count(DISTINCT entry_id) FROM occurrences').fetchone()[0]
            counts['entries_without_multiword_examples'] = counts['entries'] - counts['entries_with_multiword_examples']
            counts['distinct_headwords_with_collocations'] = db.execute("SELECT count(DISTINCT headword_key) FROM occurrences WHERE kind='collocation'").fetchone()[0]
            counts['distinct_headword_lexical_unit_ids'] = db.execute('SELECT count(DISTINCT headword_lu_id) FROM entries').fetchone()[0]
            counts['distinct_collocation_lexical_unit_ids'] = db.execute("SELECT count(DISTINCT lexical_unit_id) FROM occurrences WHERE kind='collocation'").fetchone()[0]
            counts['distinct_collocation_ids'] = db.execute("SELECT count(DISTINCT source_collocation_id) FROM occurrences WHERE kind='collocation'").fetchone()[0]
            counts['distinct_collocation_structures'] = db.execute("SELECT count(DISTINCT structure_id) FROM occurrences WHERE kind='collocation'").fetchone()[0]
            counts['collocation_occurrences_with_repeated_lu_ids'] = db.execute("SELECT coalesce(sum(n),0) FROM (SELECT count(*) n FROM occurrences WHERE kind='collocation' AND lexical_unit_id IS NOT NULL GROUP BY lexical_unit_id HAVING count(*)>1)").fetchone()[0]
            accounting['collocation_status_counts'] = dict(db.execute("SELECT coalesce(status,'<missing>'),count(*) FROM occurrences WHERE kind='collocation' GROUP BY status"))
            accounting['source_derived_examples'] = {}
            for label, condition in [('manual_missing_frequency', "o.status='manual' AND o.frequency IS NULL"),
                                     ('manual_numeric_frequency', "o.status='manual' AND o.frequency IS NOT NULL"),
                                     ('automatic', "o.status='automatic'")]:
                row = db.execute('''SELECT o.id,e.headword,e.headword_lu_id,s.source_sense_id,o.lexical_unit_id,
                    o.structure_id,o.status,o.frequency,o.logdice,o.surface,h.name,e.source_ordinal,s.source_ordinal,
                    o.source_ordinal FROM occurrences o JOIN entries e ON e.id=o.entry_id
                    JOIN senses s ON s.id=o.sense_id JOIN shards h ON h.id=e.shard_id
                    WHERE o.kind='collocation' AND ''' + condition + ' LIMIT 1').fetchone()
                if row is not None:
                    example = dict(zip(('occurrence_id','headword','headword_lexical_unit_id','sense_id','lexical_unit_id',
                        'structure_id','status','frequency','logDice','surface','shard','entry_ordinal','sense_ordinal','example_ordinal'), row))
                    example['components'] = [dict(zip(('position','num','surface','lemma','msd','source_role'), c)) for c in db.execute(
                        'SELECT position,num,surface,lemma,msd,source_role FROM components WHERE occurrence_id=? ORDER BY position', (row[0],))]
                    accounting['source_derived_examples'][label] = example
            accounting['query_plans'] = {kind: [r[3] for r in db.execute('EXPLAIN QUERY PLAN SELECT id FROM occurrences WHERE headword_key=? AND kind=? ORDER BY ' + order + ' LIMIT 50', ('otrok','collocation'))] for kind, order in (
                ('frequency', 'frequency_sort DESC,logdice_sort DESC,surface DESC,id'),
                ('logdice', 'logdice_sort DESC,frequency_sort DESC,surface DESC,id'))}
            if any(not any('SEARCH' in p and 'occurrences_' in p for p in plan) for plan in accounting['query_plans'].values()):
                raise ValueError('Headword lookup does not use a bounded index search')
            db.commit()
        manifest.update(status='READY_CANDIDATE', counts=counts, database_sha256=sha256(database),
                        database_bytes=database.stat().st_size, build_seconds=round(time.monotonic()-started,3),
                        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        accounting.update(integrity_check='PASS', foreign_key_check='PASS', row_accounting='PASS',
                          catalogue_reference_counts=acquisition.get('catalogue_reference_counts'),
                          catalogue_differences={'entries': counts['entries']-78046, 'collocation_occurrences': counts['collocations']-4425942,
                                                  'distinct_structures': counts['distinct_collocation_structures']-81},
                          shard_inventory_note='Verified ZIP contains '+str(len(files))+' XML shards; catalogue prose says 41.',
                          missing_value_policy='Retain SQL NULL/raw attributes; default API min_freq excludes NULL frequency',
                          occurrence_identity='shard + entry ordinal + sense ordinal + example ordinal; SQLite integer ID is only a local occurrence key')
        shutil.copyfile(raw / 'THIRD_PARTY_NOTICES.md', output / 'THIRD_PARTY_NOTICES.md')
        write_json(output / 'release-manifest.json', manifest)
        index = CompiledCollocations(output)
        probes = {key: index.lookup(key, min_freq=None, limit=5) for key in ('otrok','qzxqzx-nonexistent-cjvt')}
        accounting['source_derived_probes'] = probes
        write_json(output / 'qa-summary.json', accounting)
        (output / 'checksums.sha256').write_text(''.join(sha256(p)+'  '+p.name+'\n' for p in sorted(output.iterdir()) if p.is_file()))
        for path in output.iterdir():
            if path.is_file():
                path.chmod(0o440)
        print(json.dumps({'status': 'READY_CANDIDATE', 'release': str(output), 'build_id': build_id, 'counts': counts}), flush=True)
        return manifest
    except BaseException as error:
        manifest.update(status='FAILED', error_type=type(error).__name__)
        write_json(output / 'release-manifest.json', manifest)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-release', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({'mode': 'DRY_RUN', 'raw_release': str(args.raw_release), 'output': str(args.output), 'source_occurrences_deduplicated': False}))
        return 0
    os.umask(0o077)
    build(args.raw_release.resolve(strict=True), args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
