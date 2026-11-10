"""Complete-inventory arithmetic and exact presentation-only source joins."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

import pytest

from core.lexical_identity import source_occurrence_key
from pilot_backend.frequency_position import CORPUS, Reference, annotate, observation_value
from tools.build_frequency_position import build


def observation(value, corpus=CORPUS):
    return {'measure': 'frequency', 'corpus': corpus, 'scope': 'lexeme',
            'unit': 'occurrences', 'value': value, 'raw_value': str(value),
            'source_locator': 'head/measureList/measure[1]'}


@pytest.fixture
def complete(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    entries = []
    values = [[0], [5], [5], [20], [100], [], [30, 31], [-1]]
    with sqlite3.connect(source / 'sloleks.sqlite') as db:
        db.execute('CREATE TABLE entries(id INTEGER PRIMARY KEY,profile BLOB)')
        for number, frequencies in enumerate(values, 1):
            native = 'reused-publisher-id' if number in {2, 3} else 'source-' + str(number)
            entry = {'source_entry_id': native, 'lemma': 'word-' + str(number),
                'grammatical_type': 'noun', 'grammatical_features': {'type': 'common'},
                'source_status': 'AUTOMATIC' if number in {2, 5, 6} else 'MANUAL',
                'frequency_observations': [observation(value) for value in frequencies],
                'evidence': {'resource_id': 'sloleks', 'version': '3.1',
                    'source_archive_sha256': 'a' * 64, 'source_shard': 'retained.xml',
                    'entry_ordinal': number, 'source_entry_id': native}}
            entries.append(entry)
            db.execute('INSERT INTO entries VALUES (?,?)',
                (number, zlib.compress(json.dumps(entry).encode())))
    digest = hashlib.sha256((source / 'sloleks.sqlite').read_bytes()).hexdigest()
    manifest = {'status': 'READY', 'resource_id': 'sloleks', 'version': '3.1',
        'source_archive_sha256': 'a' * 64, 'database_sha256': digest, 'entries': len(entries)}
    (source / 'manifest.json').write_text(json.dumps(manifest))
    output = tmp_path / 'derived'
    result = build(source, output)
    reference = Reference(output / 'manifest.json')
    yield entries, manifest, result, reference, source
    output.chmod(0o700)


def presentation(entries, manifest):
    profile = {'facets': {'morphology': {'data': [
        {'result': {'lexemes': deepcopy(entries), 'provenance': deepcopy(manifest)}}]}}}
    bundle = {'analysis': {'version': 'klikai.lexical-collation.v1',
                           'captures': [{'response': profile}]}}
    view = {'sections': [{'items': [{'source_occurrence_key': source_occurrence_key(e['evidence']),
        'record_id': e['source_entry_id'], 'text': 'Existing source frequency'} for e in entries]}]}
    return view, bundle


def test_complete_inventory_zero_ties_and_missing_are_separate(complete):
    entries, _, result, reference, _ = complete
    assert result['counts'] == {'source_entries': 8, 'eligible': 5, 'zero': 1,
                                'missing': 1, 'invalid': 1, 'conflicting': 1}
    assert sum(bin['count'] for bin in result['bins']) == 5
    first = reference.lookup(source_occurrence_key(entries[0]['evidence']))
    assert (first['frequency'], first['rank_min'], first['rank_max']) == (0, 5, 5)
    tied = [reference.lookup(source_occurrence_key(e['evidence'])) for e in entries[1:3]]
    assert tied[0]['source_entry_id'] == tied[1]['source_entry_id']
    assert tied[0]['occurrence_key'] != tied[1]['occurrence_key']
    assert [(row['rank_min'], row['rank_max'], row['less_count'], row['equal_count'])
        for row in tied] == [(3, 4, 1, 2)] * 2
    assert [reference.lookup(source_occurrence_key(e['evidence']))['state']
        for e in entries[5:]] == ['missing', 'conflicting', 'invalid']


def test_annotation_preserves_bundle_and_view_and_includes_automatic_source(complete):
    entries, manifest, _, reference, _ = complete
    view, bundle = presentation(entries, manifest)
    before_view, before_bundle = deepcopy(view), deepcopy(bundle)
    enriched = annotate(view, bundle, reference)
    assert view == before_view and bundle == before_bundle
    rows = enriched['sections'][0]['items']
    assert [row.get('frequency_position', {}).get('frequency') for row in rows] == [0, 5, 5, 20, 100, None, None, None]
    tied = rows[1]['frequency_position']
    assert (tied['eligible_total'], tied['rank_min'], tied['rank_max']) == (5, 3, 4)
    assert (tied['percentile_low'], tied['percentile_mid'], tied['percentile_high']) == (20, 40, 60)
    assert (tied['quartile_min'], tied['quartile_max']) == (1, 3)
    assert tied['corpus'] == CORPUS and tied['population_unit'] == 'source_lexeme_occurrence'
    assert annotate(view, bundle, None) == view


@pytest.mark.parametrize('change', ['archive', 'index', 'value', 'corpus', 'scope', 'unit', 'native_id', 'grammar', 'occurrence'])
def test_incompatible_or_changed_source_cannot_borrow_position(complete, change):
    entries, manifest, _, reference, _ = complete
    view, bundle = presentation(entries[:1], manifest)
    data = bundle['analysis']['captures'][0]['response']['facets']['morphology']['data'][0]['result']
    e = data['lexemes'][0]
    if change == 'archive': e['evidence']['source_archive_sha256'] = 'b' * 64
    elif change == 'index': data['provenance']['database_sha256'] = 'b' * 64
    elif change == 'value': e['frequency_observations'][0]['value'] = 99
    elif change == 'corpus': e['frequency_observations'][0]['corpus'] = 'Different corpus'
    elif change == 'scope': e['frequency_observations'][0]['scope'] = 'surface_form_in_recorded_reading'
    elif change == 'unit': e['frequency_observations'][0]['unit'] = 'logDice'
    elif change == 'native_id': e['source_entry_id'] = 'different-native-id'
    elif change == 'grammar': e['grammatical_features']['type'] = 'proper'
    elif change == 'occurrence': e['evidence']['entry_ordinal'] = 1000
    assert annotate(view, bundle, reference) == view


def test_runtime_reads_only_derived_index_and_hashes_it_once(complete, monkeypatch):
    entries, manifest, _, reference, source = complete
    from pilot_backend import frequency_position as module
    called = []
    original = module._sha
    monkeypatch.setattr(module, '_sha', lambda path: (called.append(path), original(path))[1])
    # The runtime has no reason to open the original source index.
    (source / 'sloleks.sqlite').unlink()
    loaded = Reference(reference.manifest_path)
    view, bundle = presentation(entries, manifest)
    assert annotate(view, bundle, loaded) == annotate(view, bundle, loaded)
    assert called == [loaded.database]
    with loaded.connect() as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute('DELETE FROM positions')


def test_observation_semantics_do_not_sum_or_coerce_unknowns():
    assert observation_value([observation(0)]) == ('eligible', 0)
    assert observation_value([observation(5), observation(5)]) == ('eligible', 5)
    assert observation_value([observation(5), observation(6)]) == ('conflicting', None)
    assert observation_value([observation(None)]) == ('invalid', None)
    assert observation_value([observation(True)]) == ('invalid', None)
    assert observation_value([observation(5, 'Another corpus')]) == ('missing', None)


def test_changed_derived_database_fails_closed_without_changing_presentation(complete):
    entries, manifest, _, reference, _ = complete
    view, bundle = presentation(entries, manifest)
    reference.directory.chmod(0o700)
    reference.database.chmod(0o600)
    with sqlite3.connect(reference.database) as db:
        db.execute('UPDATE positions SET frequency=123 WHERE frequency=100')
    assert annotate(view, bundle, reference) == view
    with pytest.raises(ValueError, match='identity'):
        Reference(reference.manifest_path)
