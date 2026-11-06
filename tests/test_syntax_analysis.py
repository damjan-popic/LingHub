"""Fixed local parser transport and exact selected-text bindings; no model calls."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from core import syntax_analysis as syntax
from core.syntax_worker import records


def analysis():
    words=[{'id':1,'text':'Teče','start':0,'end':4,'lemma':'teči','upos':'VERB',
            'feats':{'VerbForm':'Fin'},'head_id':0,'deprel':'ROOT','sentence_id':1}]
    return {'schema':'linghub.syntax-analysis.v1','text':'Teče',
        'offset_convention':'unicode_codepoint_half_open','head_convention':'one_based_0_root',
        'tokens':words,'words':words,'model':{'package':syntax.MODEL_PACKAGE,
        'package_version':syntax.MODEL_VERSION,'torch_version':'observed-test-version',
        'pipeline':['tokenize','pos','lemma','depparse'],'resource_sha256':syntax.MODEL_HASHES,
        'resources_manifest_sha256':syntax.RESOURCES_SHA256}}


def test_one_cpu_isolated_process_uses_only_selected_text_and_retains_native_analysis(monkeypatch,tmp_path):
    calls=[]
    def run(argv,**kwargs):
        calls.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps(analysis()).encode())
    monkeypatch.setattr(syntax.subprocess,'run',run)
    monkeypatch.setenv('SYNTHETIC_SECRET','not-for-child')
    result=syntax.collect('Teče',{'syntax_python':sys.executable,'syntax_resources':str(tmp_path)})
    assert result['status']=='ok' and result['analysis']==analysis()
    assert result['execution']['inference_performed'] is True and len(calls)==1
    argv,kwargs=calls[0]
    assert argv==[sys.executable,'-I','-B',str(Path(syntax.__file__).with_name('syntax_worker.py')),
        '--resources',str(tmp_path)]
    assert json.loads(kwargs['input'])=={'text':'Teče'}
    assert kwargs['timeout']==45 and kwargs['env']['CUDA_VISIBLE_DEVICES']==''
    assert 'SYNTHETIC_SECRET' not in kwargs['env'] and 'PYTHONPATH' not in kwargs['env']


@pytest.mark.parametrize(('text','config','status'),[
    ('',{},'invalid_input'),('a'*3001,{},'too_long'),('Teče',{},'unavailable'),
    ('Teče',{'syntax_python':'relative/python'},'unavailable')])
def test_limits_and_missing_host_config_fail_locally_without_starting_a_process(monkeypatch,text,config,status):
    monkeypatch.setattr(syntax.subprocess,'run',lambda *a,**k:pytest.fail('No child may start'))
    assert syntax.collect(text,config)['status']==status


def test_timeout_is_request_local_and_does_not_retry(monkeypatch,tmp_path):
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv);raise subprocess.TimeoutExpired(argv,45)
    monkeypatch.setattr(syntax.subprocess,'run',run)
    result=syntax.collect('Teče',{'syntax_python':sys.executable,'syntax_resources':str(tmp_path)})
    assert result['status']=='timeout' and len(calls)==1 and result['analysis'] is None


@pytest.mark.parametrize('damage',['text','offset','head','model'])
def test_response_must_match_exact_input_offsets_heads_and_fixed_model(monkeypatch,tmp_path,damage):
    value=deepcopy(analysis())
    if damage=='text':value['text']='Different'
    elif damage=='offset':value['words'][0]['start']=1
    elif damage=='head':value['words'][0]['head_id']=2
    elif damage=='model':value['model']['package_version']='other'
    monkeypatch.setattr(syntax.subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=0,stdout=json.dumps(value).encode()))
    result=syntax.collect('Teče',{'syntax_python':sys.executable,'syntax_resources':str(tmp_path)})
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
