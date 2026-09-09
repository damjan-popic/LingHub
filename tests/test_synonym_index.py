from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.synonym_index import SynonymIndex, file_sha256
from tools.compile_synonyms import build
from tools.xml_safety import UnsafeXML


# Synthetic records use the observed CJVT 2.2 XML shape. Their document positions
# are not source IDs. Leading zeroes verify that source identifiers stay strings.
FIXTURE = '''<?xml version="1.0" encoding="UTF-8"?>
<dictionary>
 <entry><head><status>ssss-automatic</status><headword><lemma>Čas</lemma></headword>
  <lexicalUnit id="001" type="single" structure_id="1"><lexeme lemma="Čas" msd="Ncmsn">Čas</lexeme></lexicalUnit></head>
  <body><senseList><sense id="10" dummy="true"><relatedSenseList>
   <relatedSense sense_id="20" lexical_unit_id="002" type="synonym" synonymType="core" synonymScore="0.500">display text</relatedSense>
   <relatedSense sense_id="20" lexical_unit_id="002" type="synonym" synonymType="core" synonymScore="0.500">display text</relatedSense>
   <relatedSense sense_id="999" lexical_unit_id="999" type="synonym">nerazrešeno</relatedSense>
   <relatedSense sense_id="30" lexical_unit_id="003" type="antonym">source antonym</relatedSense>
  </relatedSenseList><definitionList><definition type="indicator">synthetic definition</definition></definitionList></sense>
  <sense id="11"><relatedSenseList><relatedSense sense_id="10" lexical_unit_id="001" type="synonym" synonymType="near">Čas</relatedSense></relatedSenseList>
   <labelList><label type="domain">synthetic domain</label></labelList></sense></senseList></body></entry>
 <entry><head><status>ssss-manual</status><headword><lemma>čas</lemma></headword>
  <lexicalUnit id="002" type="single" structure_id="1"><lexeme lemma="čas" msd="Ncmsn">čas</lexeme></lexicalUnit></head>
  <body><senseList><sense id="20"/></senseList></body></entry>
 <entry><head><status>default</status><headword><lemma>Čas</lemma></headword>
  <lexicalUnit id="003" type="MWE" structure_id="4"><component num="2"><lexeme lemma="čas" msd="Ncmsn">času</lexeme></component>
   <component num="1"><lexeme lemma="nov" msd="Agp">novemu</lexeme></component></lexicalUnit></head>
  <body><senseList><sense id="30"><relatedSenseList>
   <relatedSense sense_id="20" lexical_unit_id="002" type="synonym" synonymType="near">čas</relatedSense>
  </relatedSenseList></sense></senseList></body></entry>
</dictionary>'''


class SynonymIndexTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cjvt-synonym-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.xml = self.root / 'raw/source/thesaurus.xml'
        self.xml.parent.mkdir(parents=True)
        self.xml.write_text(FIXTURE)
        self.notice = self.root / 'raw/THIRD_PARTY_NOTICES.md'
        self.notice.write_text('Synthetic public-resource fixture; no downloaded content.\n')
        self.provenance = {'schema': 'lingstack.official-resource-acquisition.v1', 'status': 'VERIFIED',
            'resource_id': 'synthetic-thesaurus', 'title': 'Synthetic fixture', 'version': 'test',
            'publisher': 'Test fixture', 'handle': 'synthetic/fixture', 'landing_url': 'https://example.invalid/fixture',
            'license': 'CC0-1.0', 'provenance_kind': 'official_release_acquisition',
            'raw_location': str(self.root / 'raw'), 'notice_location': str(self.notice), 'sha256': 'a' * 64,
            'catalogue_reference_counts': {'entries': 4, 'synonyms': 4}, 'archive_members': []}
        self.pin_source()

    def pin_source(self):
        self.provenance['archive_members'] = [{'path': 'thesaurus.xml', 'directory': False,
            'bytes': self.xml.stat().st_size, 'sha256': file_sha256(self.xml)}]

    def compile(self, name='release'):
        output = self.root / name
        manifest = build(self.xml, output, self.provenance)
        return output, manifest, SynonymIndex(output)

    def test_source_id_sense_direction_and_order_are_preserved(self):
        output, manifest, index = self.compile()
        result = index.lookup_evidence('Čas')
        self.assertEqual([entry['entry_order'] for entry in result['entries']], [1, 3])
        self.assertEqual([entry['source_entry_id'] for entry in result['entries']], [None, None])
        first = result['entries'][0]
        self.assertEqual(first['lexical_units'][0]['lexical_unit_id'], '001')
        self.assertEqual(first['head_status'], 'ssss-automatic')
        self.assertEqual([sense['sense_id'] for sense in first['senses']], ['10', '11'])
        self.assertEqual(first['senses'][0]['source_attributes']['dummy'], 'true')
        relations = first['senses'][0]['relations']
        self.assertEqual([r['source_order'] for r in relations], [1, 2, 3, 4])
        self.assertEqual([r['target_lexical_unit_id'] for r in relations], ['002', '002', '999', '003'])
        self.assertEqual(relations[0]['synonym_score'], '0.500')
        self.assertEqual(relations[0]['raw_text'], 'display text')
        self.assertEqual(relations[0]['target_headword'], 'čas')
        self.assertEqual(relations[3]['relation_type'], 'antonym')
        self.assertEqual(index.lookup_evidence('čas')['count_relations'], 0,
                         'Directed source relations must not become symmetric or transitive')
        component_order = result['entries'][1]['lexical_units'][0]['content']['children']
        self.assertEqual([c['attributes']['num'] for c in component_order], ['2', '1'])
        self.assertEqual(first['senses'][0]['metadata'][0]['children'][0]['text'], 'synthetic definition')
        self.assertEqual(first['senses'][1]['metadata'][0]['children'][0]['text'], 'synthetic domain')

    def test_legacy_projection_is_exact_with_missing_group_default_only_in_projection(self):
        output, manifest, index = self.compile()
        source = index.lookup_evidence('Čas')['entries'][0]['senses'][0]['relations'][2]
        self.assertIsNone(source['synonym_type'])
        self.assertEqual(source['target_sense_id'], '999')
        self.assertFalse(source['target_sense_resolved'])
        self.assertFalse(source['target_lexical_unit_resolved'])
        projected = index.get_senses_for_lemma('  Čas  ')
        self.assertEqual(projected[0], {'sense_id': '10', 'core': ['čas', 'čas', 'nerazrešeno'],
                                       'near': [], 'antonyms': ['Čas']})
        self.assertEqual(projected[1]['near'], ['Čas'])
        self.assertEqual(index.get_senses_for_lemma('ČAS'), [])
        self.assertEqual(index.lookup_evidence(' C\u030cas ', normalized=True)['count_entries'], 3)
        self.assertEqual(index.lookup_evidence('qzxqzx')['entries'], [])
        self.assertEqual(index.lookup_evidence("' OR 1=1 --")['entries'], [])

    def test_all_occurrences_missing_references_and_catalogue_differences_are_accounted(self):
        output, manifest, index = self.compile()
        expected = {'entries': 3, 'lexical_units': 3, 'senses': 4, 'relation_occurrences': 6,
                    'synonym_occurrences': 5, 'antonym_occurrences': 1, 'duplicate_headword_strings': 1,
                    'head_status:ssss-automatic': 1, 'head_status:ssss-manual': 1, 'head_status:default': 1}
        for key, value in expected.items():
            self.assertEqual(manifest['counts'][key], value)
        qa = json.loads((output / 'qa-summary.json').read_text())
        self.assertEqual(qa['deduplicated_source_occurrences'], 0)
        self.assertEqual(qa['silently_skipped_records'], 0)
        self.assertEqual(qa['reference_diagnostics']['lexical_unit']['ids_and_occurrences'],
                         [{'source_id': '999', 'occurrences': 1}])
        self.assertEqual(qa['reference_diagnostics']['sense']['unresolved_unique_ids'], 1)
        self.assertEqual(qa['catalogue_comparison']['entries']['difference'], -1)
        self.assertEqual(qa['catalogue_comparison']['synonyms']['difference'], 1)
        self.assertTrue(all(any('INDEX' in line for line in plan) for plan in qa['query_plans'].values()))

    def test_runtime_is_read_only_and_never_needs_raw_xml_after_construction(self):
        output, manifest, index = self.compile()
        before = {p.name: (file_sha256(p), p.stat().st_mtime_ns) for p in output.iterdir()}
        self.xml.unlink()
        self.notice.unlink()
        with patch('core.synonym_index.file_sha256', side_effect=AssertionError('Repeated deep readiness hash')):
            self.assertEqual(index.readiness()['status'], 'READY')
            self.assertEqual(index.readiness()['build_id'], manifest['build_id'])
            self.assertTrue(index.get_senses_for_lemma('Čas'))
        with index._connect() as db, self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM entries')
        self.assertEqual(before, {p.name: (file_sha256(p), p.stat().st_mtime_ns) for p in output.iterdir()})

    def test_existing_output_and_changed_input_are_rejected_without_overwrite(self):
        output, manifest, index = self.compile()
        before = (output / 'release-manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exist'):
            build(self.xml, output, self.provenance)
        self.assertEqual((output / 'release-manifest.json').read_bytes(), before)
        self.xml.write_text(FIXTURE + '\n')
        with self.assertRaisesRegex(ValueError, 'differ'):
            build(self.xml, self.root / 'changed', self.provenance)
        self.assertFalse((self.root / 'changed').exists())

    def test_same_pinned_input_and_converter_produce_identical_database_bytes(self):
        first, first_manifest, _ = self.compile('first')
        second, second_manifest, _ = self.compile('second')
        self.assertEqual(first_manifest['fingerprint'], second_manifest['fingerprint'])
        self.assertEqual(first_manifest['database'], second_manifest['database'])
        self.assertEqual(file_sha256(first / 'synonyms.sqlite'), file_sha256(second / 'synonyms.sqlite'))

    def test_malformed_or_unsafe_source_never_gets_a_validated_release(self):
        sources = {'missing_id': FIXTURE.replace('id="001"', ''),
                   'duplicate_id': FIXTURE.replace('id="003"', 'id="001"'),
                   'invalid_score': FIXTURE.replace('synonymScore="0.500"', 'synonymScore="NaN"'),
                   'unknown_relation': FIXTURE.replace('type="antonym"', 'type="invented"'),
                   'truncated': FIXTURE[:-20],
                   'dtd': '<!DOCTYPE dictionary [<!ENTITY x "expanded">]>' + FIXTURE.split('?>', 1)[1]}
        for name, content in sources.items():
            with self.subTest(name=name):
                self.xml.write_text(content)
                self.pin_source()
                with self.assertRaises((ValueError, sqlite3.IntegrityError, __import__('xml.etree.ElementTree', fromlist=['ParseError']).ParseError)):
                    build(self.xml, self.root / name, self.provenance)
                self.assertFalse((self.root / name / 'release-manifest.json').exists())

    def test_missing_changed_or_unvalidated_evidence_cannot_be_ready(self):
        for label in ('database', 'qa', 'notice', 'status', 'metadata'):
            with self.subTest(label=label):
                output, manifest, index = self.compile(label)
                if label in ('database', 'qa', 'notice'):
                    filename = {'database': 'synonyms.sqlite', 'qa': 'qa-summary.json', 'notice': 'THIRD_PARTY_NOTICES.md'}[label]
                    with (output / filename).open('ab') as stream:
                        stream.write(b'changed')
                else:
                    manifest['status' if label == 'status' else 'fingerprint'] = 'changed'
                    (output / 'release-manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    SynonymIndex(output)


if __name__ == '__main__':
    unittest.main()
