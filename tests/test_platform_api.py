"""Public contracts over synthetic immutable SQLite and bounded contextual fixtures."""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch

from fastapi.testclient import TestClient
import candidate_app
from core import platform_api as api, morphology_api as morph
from klikai_api.services.morphology import MorphologyService


class MorphologyContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'lexicon.sqlite'
        c=sqlite3.connect(self.db)
        c.execute('CREATE TABLE lexicon(form,form_normalized,form_folded,lemma,lemma_normalized,msd,pos_family)')
        c.executemany('INSERT INTO lexicon VALUES(?,?,?,?,?,?,?)',[
            ('časti','časti','casti','čast','čast','N1','N'),('časti','časti','casti','čast','čast','N2','N'),
            ('časti','časti','casti','častiti','častiti','V1','V'),('casti','casti','casti','casto','casto','A1','A'),
            ('čast','čast','cast','čast','čast','N0','N'),('časteh','časteh','casteh','čast','čast','N3','N')]);c.commit();c.close()
        self.queries=[]
        @contextmanager
        def connection(lang):
            c=sqlite3.connect(self.db.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row;c.set_trace_callback(self.queries.append)
            try:yield c
            finally:c.close()
        source={'name':'fixture','version':'1','identifier':'fixture:1','url':'https://example.invalid/fixture','license':'fixture'}
        release=SimpleNamespace(manifest={'languages':{'sl':{'source':source},'it':{'source':source}}},release_fingerprint='fixturehash',normalization_version='nfkc-lower-ws-v1')
        self.service=MorphologyService('/not-used');self.service._connection=connection;self.service.load_release=lambda:release

    def test_batched_lemmas_equal_accepted_scalar_contract_and_preserve_order(self):
        tokens=['časti','CASTI','casteh','časti','absent']
        expected=[[asdict(c) for c in self.service.lemmatize_token('sl',t)] for t in tokens]
        self.queries.clear();actual=morph.lemmatize(self.service,'sl',tokens)
        for item,wanted in zip(actual['items'],expected):
            for candidate in wanted:
                candidate['msds']=list(candidate['msds']);candidate['pos_families']=list(candidate['pos_families'])
            self.assertEqual(item['candidates'],wanted)
        self.assertEqual([x['input'] for x in actual['items']],tokens)
        self.assertEqual(actual['unique_queries'],4)
        self.assertEqual(len(self.queries),2)
        self.assertEqual(actual['items'][1]['candidates'][0]['matched_by'],'exact')
        self.assertEqual(actual['items'][2]['candidates'][0]['matched_by'],'folded')

    def test_ambiguity_cap_does_not_hide_total_count(self):
        item=morph.lemmatize(self.service,'sl',['časti'],1)['items'][0]
        self.assertTrue(item['ambiguous']);self.assertTrue(item['truncated']);self.assertEqual(item['candidate_count'],2)

    def test_inflection_order_matches_accepted_and_native_msd_filter_is_exact(self):
        actual=morph.inflect(self.service,'sl','čast',2)
        self.assertEqual([x['form'] for x in actual['forms']],list(self.service.inflection_group('sl','čast',limit=2).forms))
        self.assertTrue(actual['truncated'])
        exact=morph.inflect(self.service,'sl','čast',10,'N2')
        self.assertEqual(exact['forms'],[{'form':'časti','normalized':'časti','msds':['N2'],'pos_families':['N']}])
        self.assertEqual(morph.inflect(self.service,'sl','čast',10,"N%' OR 1=1 --")['status'],'no_hit')

    def test_signatures_match_accepted_projection(self):
        expected=[asdict(x) for x in self.service.phrase_signatures('sl','časti časteh')]
        result=morph.signatures(self.service,'sl','časti časteh')
        self.assertEqual(result['signatures'],expected)


class PlatformAPITests(unittest.TestCase):
    def setUp(self):
        old=dict(candidate_app.app.state._state)
        self.addCleanup(lambda:(candidate_app.app.state._state.clear(),candidate_app.app.state._state.update(old)))
        candidate_app.app.state.resource_files={};candidate_app.app.state.resource_files_by_kind={}
        candidate_app.app.state.morphology=None;candidate_app.app.state.synonyms=None;candidate_app.app.state.collocations=None
        self.client=TestClient(candidate_app.app);self.addCleanup(self.client.close)
        self.text='🙂  otrok'
        self.annotation={'status':'ok','lang':'sl','text':self.text,'offset_convention':'unicode_codepoint_half_open',
            'engine_used':'spacy','tokens':[{'text':'🙂','lemma':'🙂','pos':'SYM','start':0,'end':1},
                {'text':'otrok','lemma':'otrok','pos':'NOUN','start':3,'end':8}],
            'issues':[],'spans':[],'provenance':{'rules_sha256':'rules'}}

    def test_explore_calls_analysis_once_and_keeps_original_codepoint_selection(self):
        with patch.object(api,'analyze_once',AsyncMock(return_value=self.annotation)) as analyze:
            response=self.client.post('/v1/explore',json={'lang':'sl','text':self.text,'selection':{'start':3,'end':8},'include':['analysis','loris','morphology']})
        self.assertEqual(response.status_code,200);analyze.assert_awaited_once()
        data=response.json();self.assertEqual(data['selection']['text'],'otrok')
        self.assertEqual(data['results']['analysis']['status'],'ok')
        self.assertEqual(data['results']['loris']['status'],'no_hit')
        self.assertEqual(data['results']['morphology']['status'],'unavailable')

    def test_missing_analyzer_does_not_become_no_hit(self):
        with patch.object(api,'analyze_once',AsyncMock(side_effect=api.AnalyzerFailure('unavailable','offline'))):
            response=self.client.post('/v1/explore',json={'lang':'sl','text':self.text,'include':['analysis','loris','synonyms']})
        self.assertEqual(response.json()['results']['analysis']['status'],'unavailable')
        self.assertEqual(response.json()['results']['loris']['status'],'unavailable')
        self.assertEqual(response.json()['results']['synonyms']['status'],'unavailable')

    def test_invalid_selection_language_empty_tokens_and_limits_rejected(self):
        for path,payload in [('/v1/explore',{'lang':'sl','text':'🙂a','selection':{'start':1,'end':3}}),
            ('/v1/morphology/lemmatize',{'lang':'sl','tokens':['a b']}),
            ('/v1/morphology/lemmatize',{'lang':'sl','tokens':[]}),
            ('/v1/morphology/lemmatize',{'lang':'de','tokens':['a']}),
            ('/v1/morphology/inflect',{'lang':'sl','lemma':'a','limit':101})]:
            self.assertEqual(self.client.post(path,json=payload).status_code,422)

    def test_host_and_body_limits_apply_before_analyzer(self):
        self.assertEqual(self.client.post('/v1/analyze',content=b'x'*131073).status_code,413)
        self.assertEqual(self.client.get('/v1/resources',headers={'Host':'attacker.example'}).status_code,400)

    def test_catalogue_reports_independent_unavailability_without_private_paths(self):
        with patch.object(api,'analyzer_request',AsyncMock(side_effect=api.AnalyzerFailure('unavailable','offline'))):
            response=self.client.get('/v1/resources')
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['analyzer_service_reachable'])
        self.assertFalse(response.json()['capabilities']['morphology'])
        self.assertNotIn('/data/',response.text)

    def test_public_metadata_removes_incidental_paths_preserving_source_ids(self):
        self.assertEqual(api.public({'raw_location':'/data/private','source':{'path':'/home/secret','handle':'11356/2090'}}),{'source':{'handle':'11356/2090'}})

    def test_selected_surface_dictionary_fallback_remains_usable_without_analyzer(self):
        reader=SimpleNamespace(manifest={'fingerprint':'thesaurus-fixture'},lookup_evidence=Mock(return_value={
            'entries':[{'headword':'otrok','senses':[{'sense_id':'sense1','relations':[]}]}],
            'count_entries':1,'count_senses':1,'count_relations':0}))
        candidate_app.app.state.synonyms=reader
        with patch.object(api,'analyze_once',AsyncMock(side_effect=api.AnalyzerFailure('unavailable','offline'))):
            response=self.client.post('/v1/explore',json={'lang':'sl','text':'otrok','include':['analysis','synonyms']})
        result=response.json()['results']
        self.assertEqual(result['analysis']['status'],'unavailable')
        self.assertEqual(result['synonyms']['status'],'ok')
        self.assertFalse(result['synonyms']['data']['contextual_candidates'])
        self.assertEqual(result['synonyms']['data']['lookups'][0]['evidence']['entries'][0]['senses'][0]['namespaced_id'],'cjvt-thesaurus:2.2:sense1')


class CurrentAnalyzerHealthTests(unittest.TestCase):
    def setUp(self):
        old=dict(candidate_app.app.state._state)
        self.addCleanup(lambda:(candidate_app.app.state._state.clear(),candidate_app.app.state._state.update(old)))
        candidate_app.app.state.resource_files={}
        candidate_app.app.state.resource_readiness={}
        candidate_app.app.state.lemmatizer_url='http://127.0.0.1:19999'
        self.client=TestClient(candidate_app.app);self.addCleanup(self.client.close)
        self.backend={'defaults':{'sl':'spacy','it':'spacy'},'engines':{
            'spacy':{'available':True,'languages':['sl','it']},
            'classla':{'available':True,'languages':['sl'],'experimental':True,
                      'model':{'fingerprint':'current-model-id','device':'cpu','path':'/data/private/model'}}}}

    def test_both_health_routes_report_live_opt_in_model_and_spacy_defaults(self):
        for route in ('/health','/v1/resources/health'):
            with self.subTest(route=route),patch.object(api,'analyzer_request',AsyncMock(return_value=self.backend))as observe,patch.object(candidate_app,'upstream_health',AsyncMock(return_value={'status':'ok'})):
                result=self.client.get(route)
                self.assertEqual(result.status_code,200)
                data=result.json();self.assertEqual(data['classla_adapter'],'AVAILABLE_OPT_IN')
                self.assertTrue(data['classla']['available']);self.assertTrue(data['classla']['opt_in_required'])
                self.assertEqual(data['classla']['model'],{'fingerprint':'current-model-id','device':'cpu'})
                self.assertEqual(data['analyzer_defaults'],{'sl':'spacy','it':'spacy'})
                self.assertEqual(data['contextual_nlp']['status'],'READY_LOCAL')
                observe.assert_awaited_once();self.assertEqual(observe.call_args.kwargs,{'timeout':2})
                self.assertEqual(observe.call_args.args[1],'/v1/capabilities')

    def test_optional_worker_unavailable_keeps_resource_health_available(self):
        self.backend['engines']['classla']['available']=False
        with patch.object(api,'analyzer_request',AsyncMock(return_value=self.backend)):
            result=self.client.get('/v1/resources/health')
        self.assertEqual(result.status_code,200);self.assertEqual(result.json()['classla_adapter'],'UNAVAILABLE')
        self.assertFalse(result.json()['classla']['available'])
        self.assertEqual(result.json()['contextual_nlp']['status'],'READY_LOCAL')

    def test_unreachable_or_invalid_capabilities_never_claim_ready(self):
        for observation in (AsyncMock(side_effect=api.AnalyzerFailure('unavailable','timeout')),
                            AsyncMock(return_value={'engines':[], 'defaults':{}})):
            with patch.object(api,'analyzer_request',observation):
                result=self.client.get('/v1/resources/health')
            self.assertEqual(result.status_code,200);self.assertEqual(result.json()['classla_adapter'],'UNAVAILABLE')
            self.assertFalse(result.json()['analyzer_service_reachable'])
            self.assertFalse(result.json()['classla']['available'])
            self.assertEqual(result.json()['analyzer_defaults'],{'sl':'spacy','it':'spacy'})


if __name__=='__main__':unittest.main()
