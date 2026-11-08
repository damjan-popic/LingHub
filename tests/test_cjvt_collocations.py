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

    def measurement_fixture(self, measurements):
        """Explicit measurement mutations of the retained source occurrence."""
        root=ET.fromstring(SOURCE)
        examples=root.find('.//exampleContainerList')
        template=deepcopy(examples[0])
        for child in list(examples):examples.remove(child)
        for index,(frequency,logdice) in enumerate(measurements):
            container=deepcopy(template);example=container.find('multiwordExample')
            example.set('lexical_unit_id',str(1000+index))
            for key,value in [('frequency',frequency),('logDice',logdice)]:
                if value is not None:example.set(key,str(value))
            examples.append(container)
        return self.compile(ET.tostring(root,encoding='unicode'))[1]

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
        self.assertEqual(ascending[0]['frequency'], 6839)
        self.assertIsNone(ascending[1]['frequency'])

    def test_known_values_zero_and_null_rank_before_native_limit_in_both_directions(self):
        index=self.measurement_fixture([(None,None)]*501+[(25,2.5),(50,5.0),(0,0.0)])
        for sort,measure in [('freq','frequency'),('logdice','logDice')]:
            for order,expected_ids in [('asc',[1503,1501,1502]),('desc',[1502,1501,1503])]:
                with self.subTest(sort=sort,order=order):
                    # Eight missing records must not conceal known25/50 or a
                    # genuine0 in an explicit request for the lowest values.
                    result=index.lookup('otrok',min_freq=None,sort=sort,order=order,limit=8)
                    rows=result['collocations']
                    self.assertEqual([r['lexical_unit_id']for r in rows[:3]],expected_ids)
                    self.assertEqual(sum(r[measure]is not None for r in rows),3)
                    self.assertTrue(all(r[measure]is None for r in rows[3:]))
                    self.assertEqual(result['resource']['ranking'],{
                        'measure':measure,'order':order,'null_policy':'known_values_first_missing_unranked_tail',
                        'scope':'returned_lookup','known_returned':3,'missing_returned':5,'status':'mixed'})
                    window=index.lookup('otrok',min_freq=None,sort=sort,order=order,limit=500)
                    self.assertEqual([r['lexical_unit_id']for r in window['collocations'][:3]],expected_ids)
                    self.assertEqual((window['count_total'],window['count_returned']),(504,500))
                    self.assertEqual(window['resource']['ranking']['missing_returned'],497)
                    self.assertNotIn('frequency',rows[-1]['source_attributes'])
                    self.assertNotIn('logDice',rows[-1]['source_attributes'])

    def test_frequency_ties_keep_measured_logdice_before_missing_in_both_directions(self):
        index=self.measurement_fixture([(25,None),(25,0.0),(25,5.0)])
        for order,expected in [('asc',[0.0,5.0,None]),('desc',[5.0,0.0,None])]:
            rows=index.lookup('otrok',min_freq=None,sort='freq',order=order)['collocations']
            self.assertEqual([r['logDice']for r in rows],expected)
            self.assertEqual([r['frequency']for r in rows],[25]*3)

    def test_logdice_ties_keep_measured_frequency_before_missing_in_both_directions(self):
        index=self.measurement_fixture([(None,2.5),(0,2.5),(50,2.5)])
        for order,expected in [('asc',[0,50,None]),('desc',[50,0,None])]:
            rows=index.lookup('otrok',min_freq=None,sort='logdice',order=order)['collocations']
            self.assertEqual([r['frequency']for r in rows],expected)
            self.assertEqual([r['logDice']for r in rows],[2.5]*3)

    def test_all_missing_measurements_remain_returned_and_explicitly_unranked(self):
        index=self.measurement_fixture([(None,None)]*10)
        for sort,measure in [('freq','frequency'),('logdice','logDice')]:
            for order in ['asc','desc']:
                result=index.lookup('otrok',min_freq=None,sort=sort,order=order,limit=8)
                self.assertEqual((result['count_total'],result['count_returned']),(10,8))
                self.assertTrue(all(r['frequency']is None and r['logDice']is None for r in result['collocations']))
                self.assertEqual(result['resource']['ranking'],{
                    'measure':measure,'order':order,'null_policy':'known_values_first_missing_unranked_tail',
                    'scope':'returned_lookup','known_returned':0,'missing_returned':8,'status':'unranked'})
        absent=index.lookup('not-recorded',min_freq=None)
        self.assertEqual(absent['resource']['ranking']['status'],'empty')

    def test_numeric_filters_still_exclude_missing_and_preserve_raw_measurements(self):
        index=self.measurement_fixture([(None,None),(0,0.0),(25,2.5),(50,5.0)])
        default=index.lookup('otrok',sort='freq',order='asc')
        self.assertEqual([r['frequency']for r in default['collocations']],[25,50])
        self.assertEqual(default['resource']['ranking']['status'],'ranked')
        filtered=index.lookup('otrok',min_freq=None,min_logdice=2.0,sort='logdice',order='asc')
        self.assertEqual([r['logDice']for r in filtered['collocations']],[2.5,5.0])

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
            plans=[r[3] for r in db.execute("EXPLAIN QUERY PLAN SELECT id FROM occurrences WHERE headword_key='otrok' AND kind='collocation' ORDER BY (frequency IS NULL) ASC,frequency DESC,(logdice IS NULL) ASC,logdice DESC,surface DESC,id LIMIT 50")]
            self.assertTrue(any('SEARCH' in r and 'occurrences_' in r for r in plans))
        with sqlite3.connect(self.root/'collocations.sqlite') as db:
            db.execute("UPDATE occurrences SET frequency=1 WHERE id=1")
        with self.assertRaisesRegex(ValueError,'identity'):
            CompiledCollocations(self.root)


if __name__=='__main__':
    unittest.main()
