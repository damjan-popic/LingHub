"""Existing-worker transport and exact selected-text bindings; no model calls."""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from core import syntax_analysis as syntax
from core.syntax_worker import records

CONFIG = {'syntax_url': 'http://127.0.0.1:18316'}


def analysis():
    words=[{'id':1,'text':'Teče','start':0,'end':4,'lemma':'teči','upos':'VERB',
            'feats':{'VerbForm':'Fin'},'head_id':0,'deprel':'ROOT','sentence_id':1}]
    return {'schema':'linghub.syntax-analysis.v1','text':'Teče',
        'offset_convention':'unicode_codepoint_half_open','head_convention':'one_based_0_root',
        'tokens':words,'words':words,'model':{'package':syntax.MODEL_PACKAGE,
        'package_version':syntax.MODEL_VERSION,'torch_version':'observed-test-version',
        'pipeline':['tokenize','pos','lemma','depparse'],'resource_sha256':syntax.MODEL_HASHES,
        'resources_manifest_sha256':syntax.RESOURCES_SHA256}}


def test_one_loopback_request_uses_only_selected_text_and_retains_native_analysis(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'status':'ok','analysis':analysis()})
    monkeypatch.setenv('HTTP_PROXY','http://must-not-be-used.invalid:8080')
    result=syntax.collect('Teče',CONFIG,transport=httpx.MockTransport(handler))
    assert result['status']=='ok' and result['analysis']==analysis()
    assert result['execution']['inference_performed'] is True and len(calls)==1
    assert result['execution']['process_started'] is False
    assert result['execution']['transport']=='existing_classla_worker'
    assert str(calls[0].url)=='http://127.0.0.1:18316/syntax'
    assert json.loads(calls[0].content)=={'text':'Teče'}
    assert calls[0].extensions['timeout']['read']==30
    assert calls[0].extensions['timeout']['connect']==2


@pytest.mark.parametrize(('text','config','status'),[
    ('',{},'invalid_input'),('a'*3001,{},'too_long'),('Teče',{},'unavailable'),
    ('Teče',{'syntax_url':'https://127.0.0.1:18316'},'unavailable'),
    ('Teče',{'syntax_url':'http://other.example:18316'},'unavailable'),
    ('Teče',{'syntax_url':'http://user:secret@127.0.0.1:18316'},'unavailable'),
    ('Teče',{'syntax_url':'http://127.0.0.1:18316/other'},'unavailable'),
    ('Teče',{'syntax_url':'http://127.0.0.1:18316/?x=1'},'unavailable'),
    ('Teče',{'syntax_url':'http://127.0.0.1:bad'},'unavailable')])
def test_limits_and_missing_host_config_fail_locally_without_dispatch(text,config,status):
    transport=httpx.MockTransport(lambda request:pytest.fail('No request may start'))
    result=syntax.collect(text,config,transport=transport)
    assert result['status']==status and not result['execution']['attempted']


@pytest.mark.parametrize(('error','status'),[
    (httpx.ReadTimeout('private details'),'timeout'),
    (httpx.ConnectError('private details'),'unavailable')])
def test_transport_failure_is_request_local_and_does_not_retry(error,status):
    calls=[]
    def handler(request):
        calls.append(request);raise error
    result=syntax.collect('Teče',CONFIG,transport=httpx.MockTransport(handler))
    assert result['status']==status and len(calls)==1 and result['analysis'] is None
    assert 'private details' not in json.dumps(result)


@pytest.mark.parametrize(('code','status'),[(302,'unavailable'),(503,'unavailable'),(504,'timeout')])
def test_redirect_busy_and_worker_timeout_do_not_retry_or_fall_back(code,status):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(code,headers={'Location':'http://other.example/'})
    result=syntax.collect('Teče',CONFIG,transport=httpx.MockTransport(handler))
    assert result['status']==status and len(calls)==1 and result['analysis'] is None


@pytest.mark.parametrize('damage',['text','offset','head','model'])
def test_response_must_match_exact_input_offsets_heads_and_fixed_model(damage):
    value=deepcopy(analysis())
    if damage=='text':value['text']='Different'
    elif damage=='offset':value['words'][0]['start']=1
    elif damage=='head':value['words'][0]['head_id']=2
    elif damage=='model':value['model']['package_version']='other'
    result=syntax.collect('Teče',CONFIG,transport=httpx.MockTransport(lambda request:
        httpx.Response(200,json={'status':'ok','analysis':value})))
    assert result['status']=='malformed_response' and result['analysis'] is None


def test_malformed_or_oversize_response_never_becomes_an_analysis():
    for content in [b'{',b'x'*(syntax.MAX_RESPONSE_BYTES+1)]:
        result=syntax.collect('Teče',CONFIG,transport=httpx.MockTransport(lambda request:
            httpx.Response(200,content=content)))
        assert result['status']=='malformed_response' and result['analysis'] is None


def test_classla_serialization_preserves_repetitions_and_rejects_expanded_word_offsets():
    first=SimpleNamespace(id=1,text='otrok',lemma='otrok',upos='NOUN',feats='Case=Nom',head=0,deprel='root')
    second=SimpleNamespace(id=2,text='otrok',lemma='otrok',upos='NOUN',feats='Case=Nom',head=1,deprel='dep')
    tokens=[SimpleNamespace(text='otrok',start_char=0,end_char=5,words=[first]),
            SimpleNamespace(text='otrok',start_char=0,end_char=5,words=[second])]
    doc=SimpleNamespace(sentences=[SimpleNamespace(words=[first,second],tokens=tokens)])
    value=records(doc,' otrok\n otrok ')
    assert [(w['start'],w['end'],w['head_id'])for w in value]==[(1,6,0),(8,13,1)]
    assert all(w['offset_mapping']=='monotonic_exact_after_whitespace'for w in value)
    tokens[0].words=[first,second]
    with pytest.raises(ValueError,match='word_alignment_unavailable'):records(doc,' otrok\n otrok ')
