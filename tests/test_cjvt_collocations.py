"""Source-derived KSSS cases plus explicit corruption/order/duplicate mutations.

The small examples below come from collocations_export_0_2000.xml, SHA256
d4af46b5789f7cd65a7dac8be51ecff539e97916914eef64c1fe3aa8532ddede,
official CJVT KSSS 2.2, handle11356/2090, CC-BY-SA-4.0.
"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET

from core.compiled_collocations import CompiledCollocations, NORMALIZATION, sha256
from tools.build_cjvt_collocations import SCHEMA, INDEXES, compile_shards

SOURCE = '''<dictionary><entry><head><status>kssj-automatic</status><headword><lemma>otrok</lemma></headword>
<lexicalUnit id="90" type="single" structure_id="1"><lexeme lemma="otrok" msd="Ncmsn">otrok</lexeme></lexicalUnit></head>
<body><senseList><sense id="16268462"><exampleContainerList>
<exampleContainer><multiwordExample lexical_unit_id="12250092" type="collocation" structure_id="34" status="manual">
<comp num="1" lemma="šolski" msd="Agpmpn">šolski</comp><comp num="2" lemma="otrok" msd="Ncmpn">otroci</comp>
</multiwordExample></exampleContainer>
<exampleContainer><multiwordExample lexical_unit_id="12250099" type="grammaticalCombination" structure_id="921" status="manual">
<comp num="1" lemma="otrok" msd="Ncmsn">otrok</comp><comp num="2" lemma="z" msd="Si">s</comp>
<comp num="3" lemma="poseben" msd="Agpfpi">posebnimi</comp><comp num="4" lemma="potreba" msd="Ncfpi">potrebami</comp>
</multiwordExample></exampleContainer>
<exampleContainer><multiwordExample lexical_unit_id="9561883" type="collocation" structure_id="106" frequency="6839" logDice="12.10279" status="manual">
<comp num="1" lemma="otrok" msd="Ncmpn">otroci</comp><comp num="2" lemma="in" msd="Cc">in</comp><comp num="3" lemma="mladostnik" msd="Ncmpn">mladostniki</comp>
</multiwordExample></exampleContainer></exampleContainerList></sense></senseList></body></entry></dictionary>'''


class CollocationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def compile(self, text=SOURCE):
        source = self.root / 'collocations_export_fixture.xml'
        source.write_text(text)
        database = self.root / 'collocations.sqlite'
        with closing(sqlite3.connect(database)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript(SCHEMA)
            db.execute('INSERT INTO metadata VALUES (?,?)', ('build_id','fixture-build'))
            counts = compile_shards(db, [(source, {'path': source.name, 'sha256': sha256(source), 'bytes': source.stat().st_size})])
            db.executescript(INDEXES)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            db.commit()
        manifest = {'status':'READY_CANDIDATE','resource_kind':'collocations','schema_version':1,
                    'normalization':NORMALIZATION,'build_id':'fixture-build','database_sha256':sha256(database),
                    'counts':counts['counts'],'source':{'handle':'11356/2090','archive_sha256':'fixture-source-derived'}}
        (self.root / 'release-manifest.json').write_text(json.dumps(manifest))
        return counts, CompiledCollocations(self.root)

    def test_source_ids_components_nullable_measurements_and_type_accounting(self):
        accounting, index = self.compile()
        self.assertEqual(accounting['counts'], {'entries':1,'senses':1,'multiword_occurrences':3,'collocations':2,'components':9,'other_examples':0})
        self.assertEqual(accounting['kinds'], {'collocation':2,'grammaticalCombination':1})
        all_records = index.lookup('OTROK', min_freq=None)['collocations']
        manual = next(r for r in all_records if r['lexical_unit_id']==12250092)
        self.assertIsNone(manual['frequency'])
        self.assertIsNone(manual['logDice'])
        self.assertIsNone(manual['collocation_id'])
        self.assertEqual(manual['sense_id'], '16268462')
        self.assertEqual(manual['headword_lexical_unit_id'], '90')
        self.assertEqual([c['text'] for c in manual['components']], ['šolski','otroci'])
        self.assertEqual([c['lemma'] for c in manual['components']], ['šolski','otrok'])
        filtered=index.lookup('otrok')
        self.assertEqual((filtered['count_total'],filtered['count_returned']), (2,1))
        self.assertEqual(filtered['collocations'][0]['frequency'], 6839)
        self.assertEqual(index.lookup('qzxqzx-absent')['count_total'], 0)

    def test_explicit_order_mutation_does_not_sort_by_component_num(self):
        modified = SOURCE.replace('<comp num="1" lemma="šolski"', '<comp num="9" lemma="šolski"')
        _, index = self.compile(modified)
        item = next(r for r in index.lookup('otrok',min_freq=None)['collocations'] if r['lexical_unit_id']==12250092)
        self.assertEqual([c['num'] for c in item['components']], [9,2])
        self.assertEqual(item['surface'], 'šolski otroci')

    def test_filters_keep_unfiltered_total_and_do_not_include_grammatical_combinations(self):
        _, index = self.compile()
        result = index.lookup(' otrok ', min_freq=None, status='MANUAL', structure_id=34)
        self.assertEqual((result['count_total'], result['count_returned']), (2, 1))
        self.assertEqual(result['collocations'][0]['lexical_unit_id'], 12250092)
        self.assertEqual(index.lookup('otrok', min_freq=None, structure_id=921)['count_returned'], 0)
        self.assertEqual(index.lookup('otrok', min_freq=None, min_logdice=13)['count_returned'], 0)
        self.assertEqual(index.lookup("otrok' OR 1=1 --", min_freq=None)['count_total'], 0)
        ascending = index.lookup('otrok', min_freq=None, order='asc')['collocations']
        self.assertIsNone(ascending[0]['frequency'])
        self.assertEqual(ascending[1]['frequency'], 6839)

    def test_repeated_source_lu_in_another_entry_is_not_deduplicated(self):
        root=ET.fromstring(SOURCE)
        root.append(deepcopy(root[0]))
        accounting, index = self.compile(ET.tostring(root,encoding='unicode'))
        self.assertEqual(accounting['counts']['collocations'],4)
        records=index.lookup('otrok',min_freq=None)['collocations']
        repeated=[r for r in records if r['lexical_unit_id']==12250092]
        self.assertEqual(len(repeated),2)
        self.assertEqual(len({r['occurrence_id'] for r in repeated}),2)
        self.assertEqual({r['provenance']['entry_ordinal'] for r in repeated},{1,2})

    def test_malformed_numeric_field_fails_instead_of_becoming_missing(self):
        with self.assertRaisesRegex(ValueError,'Malformed numeric'):
            self.compile(SOURCE.replace('frequency="6839"','frequency="bad"'))

    def test_missing_components_fail_instead_of_skipping_occurrence(self):
        root=ET.fromstring(SOURCE)
        for comp in list(root.find('.//multiwordExample')):
            root.find('.//multiwordExample').remove(comp)
        with self.assertRaisesRegex(ValueError,'Malformed multiword components'):
            self.compile(ET.tostring(root,encoding='unicode'))

    def test_readonly_adapter_uses_index_and_rejects_modified_database(self):
        _, index = self.compile()
        with closing(index.connect()) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM occurrences')
            plans=[r[3] for r in db.execute("EXPLAIN QUERY PLAN SELECT id FROM occurrences WHERE headword_key='otrok' AND kind='collocation' ORDER BY frequency_sort DESC,logdice_sort DESC,surface DESC,id LIMIT 50")]
            self.assertTrue(any('SEARCH' in r and 'occurrences_frequency' in r for r in plans))
        with sqlite3.connect(self.root/'collocations.sqlite') as db:
            db.execute("UPDATE occurrences SET frequency=1 WHERE id=1")
        with self.assertRaisesRegex(ValueError,'identity'):
            CompiledCollocations(self.root)


if __name__=='__main__':
    unittest.main()
