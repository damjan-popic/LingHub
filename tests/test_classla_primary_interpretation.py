"""Operation counts and rule applicability; synthetic data is explicitly local."""
import asyncio
import copy
from types import SimpleNamespace as NS
from unittest.mock import patch

import httpx
import pytest

from core import analysis_profiles as profiles
from core.lexical_profile import ProfileRequest, make_profile
from core.phrase_interpretation import interpret_phrase
from core.platform_api import AnalyzeRequest, AnalyzerFailure

PIN = '6a12a8f0996bf623fa97f11455f1b1509fb06c6eaffe0c49c05a0bf8036866c1'
RULES = '0dee12b10298bb381f09f1b3c454a477df09f04eaafa6364a174f5b537511d1c'


def native(text, engine='classla'):
    words = []; tokens = []; cursor = 0
    for i, word in enumerate(text.split()):
        a = text.index(word, cursor); cursor = a + len(word)
        tokens.append({'id':i,'sentence_id':0,'start':a,'end':cursor,'text':word,'word_ids':[i]})
        words.append({'id':i,'parent_token_id':i,'sentence_id':0,'text':word,'lemma':word,
                      'upos':'NOUN','xpos':'N','feats':{},'start':a,'end':cursor,'mapping_quality':'exact_surface'})
    model = {'engine':engine,'version':'2.2.3','fingerprint':PIN}
    if engine == 'spacy':
        model.update(version='3.8.16',model_version='3.8.0',fingerprint='3105e19bd0d05135671164ee4bea8bb9cf3b6e624decbc16a9a3aa9a2a8185da')
    return {'model':model,'tokens':tokens,'words':words,'sentences':[{'id':0,'start':0,'end':len(text)}]}


class Dictionaries:
    manifest = {'source':{},'fingerprint':'synthetic','build_id':'synthetic'}
    def __init__(self): self.synonyms = []; self.collocations = []
    def lookup_evidence(self, lemma, *, normalized=False):
        assert normalized is True
        self.synonyms.append(lemma)
        return {'entries':[]}
    def get_collocations_for_lemma(self, lemma, **kwargs):
        self.collocations.append(lemma)
        return {'collocations':[],'count_returned':0,'count_total':0}


def request(dictionary=None):
    return NS(headers={},app=NS(state=NS(lexical_classla_url='http://classla.test',
        lemmatizer_url='http://matcher.test',synonyms=dictionary,collocations=dictionary,
        sloleks_profiles=None,morphology=None)))


def run_analysis(engine, lang='sl', text='lesenka', fail=None, include_loris=True):
    counts = []
    async def analyzer(req, route, payload, **kwargs):
        counts.append(route)
        if fail == 'loris' and route.endswith('from-analysis'):
            raise AnalyzerFailure('unavailable','fixture matcher unavailable')
        if route.endswith('from-analysis'):
            assert payload['analysis'] == native(text)
            return {'text':text,'lang':'sl','issues':[],'spans':[], 'provenance':{'rules_sha256':RULES}}
        assert payload['engine'] == 'spacy'
        return {'analysis':native(text,'spacy'),'tokens':[],'issues':[],'spans':[],
                'provenance':{'rules_sha256':RULES}}
    def http(req):
        counts.append('classla')
        return httpx.Response(503 if fail == 'classla' else 200,json={'analysis':native(text)})
    factory = httpx.AsyncClient
    with patch('core.platform_api.analyzer_request',side_effect=analyzer), patch.object(profiles.httpx,'AsyncClient',side_effect=lambda **kw:factory(transport=httpx.MockTransport(http),**kw)):
        result = asyncio.run(profiles.analyze(request(),AnalyzeRequest(lang=lang,text=text,engine=engine,include_loris=include_loris)))
    return result, counts


def test_primary_uses_one_classla_and_reuses_same_annotations_for_loris():
    result, counts = run_analysis(profiles.PRIMARY_PROFILE)
    assert counts == ['classla','/v1/loris/from-analysis']
    assert result['engines_used'] == ['classla']
    assert result['operations']['trained_spacy_calls'] == 0
    assert result['raw_engines']['classla']['analysis'] == native('lesenka')


def test_primary_failure_never_invokes_spacy_and_loris_failure_is_partial():
    with pytest.raises(AnalyzerFailure): run_analysis(profiles.PRIMARY_PROFILE,fail='classla')
    result, counts = run_analysis(profiles.PRIMARY_PROFILE,fail='loris')
    assert result['status'] == 'partial' and result['loris_status'] == 'unavailable'
    assert '/v1/analyze' not in counts


@pytest.mark.parametrize('engine',['compare','klikai_experimental_v1','linghub_lexical_stable_v1'])
def test_comparison_and_legacy_profiles_retain_second_engine(engine):
    result, counts = run_analysis(engine)
    assert sorted(counts) == ['/v1/analyze','classla']
    assert set(result['engines_used']) == {'classla','spacy'}


def test_italian_retains_spacy_and_optional_loris_can_be_disabled():
    result, counts = run_analysis(profiles.PRIMARY_PROFILE,'it')
    assert counts == ['/v1/analyze'] and result['engine_used'] == 'spacy'
    result, counts = run_analysis(profiles.PRIMARY_PROFILE,include_loris=False)
    assert counts == ['classla'] and result['loris_status'] == 'not_attempted'


def test_scoped_reuse_reports_no_new_operations_and_does_not_cross_scopes():
    req = AnalyzeRequest(lang='sl',text='synthetic scope test',engine=profiles.PRIMARY_PROFILE)
    one=request();one.headers={'x-linghub-cache-scope':'scope-one'}
    two=request();two.headers={'x-linghub-cache-scope':'scope-two'}
    profiles._CACHE.clear()
    async def execute(*args):
        return {'degraded':False,'operations':{'classla_calls':1,'trained_spacy_calls':0,'loris_reused_analysis_calls':1}}
    with patch.object(profiles,'_analyze',side_effect=execute) as runner:
        first=asyncio.run(profiles.analyze(one,req));second=asyncio.run(profiles.analyze(one,req))
        third=asyncio.run(profiles.analyze(two,req))
    assert runner.call_count == 2
    assert first['operations']['classla_calls']==third['operations']['classla_calls']==1
    assert second['cache']['status']=='hit' and set(second['operations'].values())=={0}
    profiles._CACHE.clear()


def test_whole_profile_dictionary_and_phrase_helpers_have_no_hidden_nlp_calls():
    dictionary = Dictionaries(); counts = []; text = 'modrem stolu'
    annotation = native(text); annotation['words'][0]['upos'] = 'ADJ'
    async def analyzer(req, route, payload, **kwargs):
        counts.append(route); assert route == '/v1/loris/from-analysis'
        assert payload['analysis'] == annotation
        return {'text':text,'lang':'sl','issues':[],'spans':[],'provenance':{'rules_sha256':RULES}}
    def http(req):
        counts.append('classla')
        return httpx.Response(200,json={'analysis':annotation})
    factory = httpx.AsyncClient
    with patch('core.platform_api.analyzer_request',side_effect=analyzer), patch.object(profiles.httpx,'AsyncClient',side_effect=lambda **kw:factory(transport=httpx.MockTransport(http),**kw)):
        value = asyncio.run(make_profile(ProfileRequest(text=text,lang='sl',profile=profiles.PRIMARY_PROFILE),request(dictionary)))
    assert counts == ['classla','/v1/loris/from-analysis']
    assert dictionary.synonyms and dictionary.collocations
    assert value['analysis']['operations']['trained_spacy_calls'] == 0


def phrase(text='modrem stolu', adjective_case='locative', noun_cases=('dative','locative')):
    words = text.split(); position = 0; tokens = []
    for i, word in enumerate(words):
        pos = 'ADJ' if i == len(words)-2 else 'NOUN' if i == len(words)-1 else 'ADP'
        tokens.append({'text':word,'upos':pos,'start':position,'end':position+len(word)})
        position += len(word)+1
    forms = {}
    for word, category, cases in ((words[-2],'adjective',[adjective_case]),(words[-1],'noun',noun_cases)):
        forms[word] = {'recorded_readings':[{'lemma':word,'grammatical_type':category,'source_entry_id':'fixture:'+word,
            'source_locator':'fixture-slot:'+str(i),'slot_locator':i,'exact_surface':True,
            'features':{'case':case,'number':'singular','gender':'masculine','type':'common' if category=='noun' else 'general'}} for i,case in enumerate(cases)]}
    return {'engines':{'classla':{'tokens':tokens}},'recorded_forms':forms,'submitted_expression':text}


def test_intersection_uses_recorded_slots_not_analyzer_case_and_keeps_raw():
    body = phrase(); before = copy.deepcopy(body)
    fact = interpret_phrase(body,'modrem stolu')['supported_derivations'][0]
    assert body == before
    assert fact['readings'] == [{'case':'locative','number':'singular','gender':'masculine'}]
    assert fact['fact_class'] == 'supported_application' and len(fact['source_forms']) == 3
    assert fact['rule_source']['source_id'] == 'slo-na-dlani:147'


def test_dative_malformed_and_unattached_neighbor_counterexamples():
    body = phrase(adjective_case='dative')
    assert interpret_phrase(body,'modrem stolu')['supported_derivations'][0]['readings'][0]['case'] == 'dative'
    body = phrase(adjective_case='nominative')
    assert interpret_phrase(body,'modrem stolu')['supported_derivations'][0]['status'] == 'incompatible'
    body['recorded_forms']['stolu']['candidate_inventory_truncated'] = True
    assert not interpret_phrase(body,'modrem stolu')['supported_derivations']
    body = phrase('glej modrem stolu'); body['engines']['classla']['tokens'][0]['upos'] = 'VERB'
    assert not interpret_phrase(body,'glej modrem stolu')['supported_derivations']


def test_adposition_context_quotes_homographs_and_true_ambiguity():
    body = phrase('na modrem stolu')
    assert interpret_phrase(body,'na modrem stolu')['supported_derivations'][0]['construction']['basis'] == 'adpositional phrase'
    body = phrase(); extra = copy.deepcopy(body['recorded_forms']['stolu']['recorded_readings'][0]);extra['features']['type']='proper';extra['source_entry_id']='fixture:proper'
    body['recorded_forms']['stolu']['recorded_readings'].append(extra)
    result = interpret_phrase(body,'modrem stolu')['supported_derivations'][0]
    assert 'fixture:proper' not in result['source_ids']
    adjective = copy.deepcopy(body['recorded_forms']['modrem']['recorded_readings'][0]);adjective['features']['case']='dative'
    body['recorded_forms']['modrem']['recorded_readings'].append(adjective)
    assert interpret_phrase(body,'modrem stolu')['supported_derivations'][0]['status'] == 'ambiguous'
    body = phrase();body['engines']['classla']['tokens'] = body['engines']['classla']['tokens'][1:]
    assert not interpret_phrase(body,'modrem stolu')['supported_derivations']
