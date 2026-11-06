"""Transport/alignment fixtures for the actual LORIS id/text token contract.

Responses are synthetic, not claims about CJVT prediction quality. MockTransport
never sends text or credentials to the network.
"""
import asyncio
from dataclasses import replace
import json
import re

import httpx
import pytest

from core.comma_checker import CommaCheckerConfig, ENDPOINT, OFFSET_CONVENTION, check, public_proposal


def response(original, target, *, normalized_whitespace=False):
    def tokens(text, prefix):
        result = []
        for index, match in enumerate(re.finditer(r'\S+\s*', text)):
            value = match.group()
            if normalized_whitespace:
                value = re.sub(r'\s+', ' ', value)
            result.append({'id': prefix+str(index+1), 'text': value})
        return result
    source, proposed = tokens(original, 's'), tokens(target, 't')
    assert len(source) == len(proposed), 'Use explicit groups for changed token counts'
    corrections = {}
    for old, new in zip(source, proposed):
        if old['text'] != new['text']:
            corrections[old['id']] = {'source_start': old['id'], 'source_end': old['id'],
                'corrections': [{'source_id': old['id'], 'source_text': old['text'], 'text': new['text']}]}
    return {'source_tokens': source, 'target_tokens': proposed, 'corrections': corrections}


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path/'checker.key'
    path.write_text('fixture-private-key\n')
    path.chmod(0o600)
    return path


def run(text, raw, key_file, **settings):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=raw)
    config = CommaCheckerConfig(key_file=key_file, transport=httpx.MockTransport(handler), **settings)
    return asyncio.run(check(text, config)), requests


@pytest.mark.parametrize(('original','target'),[
    ('Ko pridem domov bom poklical.', 'Ko pridem domov, bom poklical.'),
    ('Rekel je, da pride, in da prinese kruh.', 'Rekel je, da pride in da prinese kruh.'),
    ('Pozimi in kadar je mraz, moramo zakuriti.', 'Pozimi in kadar je mraz, moramo zakuriti.'),
    ('🙂 Ko pridem domov bom poklical.', '🙂 Ko pridem domov, bom poklical.'),
    ('»Ko pridem domov bom poklical.«', '»Ko pridem domov, bom poklical.«'),
])
def test_one_optional_prediction_preserves_target_and_truthful_execution(original,target,key_file):
    result, requests = run(original, response(original,target), key_file)
    assert result['status'] == 'ok'
    assert result['input_text'] == original and result['proposed_text'] == target
    assert result['checked_extent'] == {'start':0,'end':len(original),
        'offset_convention':OFFSET_CONVENTION,'complete':True}
    assert result['reported_model_version'] is None and result['reported_confidence'] is None
    assert result['execution'] == {'invocation_attempted':True,'invocation_acknowledged':True,
        'response_received':True,'http_status':200,'neural_prediction_received':True,
        'inference_performed':None}
    assert len(requests) == 1 and str(requests[0].url) == ENDPOINT
    assert json.loads(requests[0].content) == {'vhodna_poved':original}
    assert requests[0].headers['X-API-KEY'] == 'fixture-private-key'
    assert 'fixture-private-key' not in json.dumps(result)
    assert 'confident' not in json.dumps(result)


def test_repeated_occurrence_and_unicode_offsets_are_source_id_bound(key_file):
    original='🙂 Ko grem domov bom poklical ko grem domov bom pisal.'
    target='🙂 Ko grem domov bom poklical ko grem domov, bom pisal.'
    result,_=run(original,response(original,target),key_file)
    assert result['status']=='ok'
    assert result['edits']==[{'start':original.rfind('domov')+5,'end':original.rfind('domov')+5,
        'original':'','replacement':',','kind':'comma_addition'}]


def test_normalized_provider_boundary_spaces_do_not_rewrite_original_crlf(key_file):
    original='Ko pridem domov\r\nbom poklical.\r\n'
    target='Ko pridem domov,\r\nbom poklical.\r\n'
    result,_=run(original,response(original,target,normalized_whitespace=True),key_file)
    assert result['status']=='ok' and result['proposed_text']==target
    assert result['raw_result']['source_tokens'][2]['text']=='domov '
    assert all('\r'not in e['original']+e['replacement']and '\n'not in e['original']+e['replacement']for e in result['edits'])


def test_bounded_adjacent_spacing_has_exact_original_range(key_file):
    original='Zdaj , ko pridem bom poklical.'
    target='Zdaj, ko pridem bom poklical.'
    raw={'source_tokens':[{'id':'s1','text':'Zdaj , '},{'id':'s2','text':'ko pridem bom poklical.'}],
        'target_tokens':[{'id':'t1','text':'Zdaj, '},{'id':'t2','text':'ko pridem bom poklical.'}],
        'corrections':{'g1':{'source_start':'s1','corrections':[
            {'source_id':'s1','source_text':'Zdaj , ','text':'Zdaj, '}]}}}
    result,_=run(original,raw,key_file)
    assert result['status']=='ok'and result['proposed_text']==target
    assert result['edits'][0]['start']==4 and result['edits'][0]['original']==' '


@pytest.mark.parametrize(('original','target'),[
    ('Vrednost je 3,14 evra.', 'Vrednost je 314 evra.'),
    ('Vrednost je 314 evra.', 'Vrednost je 3,14 evra.'),
    ('Poglej https://example.org/a,b nato nadaljuj.', 'Poglej https://example.org/ab nato nadaljuj.'),
    ('Koda `x,y` je primer.', 'Koda `xy` je primer.'),
    ('Oznaka <a data-x="a,b">besedilo</a> ostane.', 'Oznaka <a data-x="ab">besedilo</a> ostane.'),
    ('Ko pridem domov ne bom poklical.', 'Ko pridem domov že bom poklical.'),
    ('Ko pridem domov bom poklical.', 'Ko pridem Domov bom poklical.'),
    ('Ko pridem domov bom poklical.', 'Ko pridem domov; bom poklical.'),
])
def test_numeric_protected_or_noncomma_changes_fail_locally(original,target,key_file):
    raw=response(original,target)
    result,_=run(original,raw,key_file)
    assert result['status']=='alignment_failed'
    assert result['input_text']==original and result['proposed_text']is None and result['edits']==[]
    assert result['raw_result']==raw and not result['checked_extent']['complete']
    assert result['execution']['invocation_acknowledged']
    assert result['execution']['neural_prediction_received']


def test_explicit_protected_source_quote_and_word_boundaries(key_file):
    original='Ko pridem domov bom poklical.'
    target='Ko pridem domov, bom poklical.'
    result,_=run(original,response(original,target),key_file,protected_spans=((0,len(original)),))
    assert result['status']=='alignment_failed'
    raw=response(original,target)
    raw['target_tokens'][0]['text']='Kopridem '
    result,_=run(original,raw,key_file)
    assert result['status']=='alignment_failed'


def test_incomplete_or_changed_source_and_inconsistent_target_fail_locally(key_file):
    original='Ko pridem domov bom poklical.'
    raw=response(original,original)
    raw['source_tokens'][0]['text']='Če '
    result,_=run(original,raw,key_file)
    assert result['status']=='alignment_failed'
    raw=response(original,original)
    raw['target_tokens'][2]['text']='domov, '
    result,_=run(original,raw,key_file)
    assert result['status']=='alignment_failed'


@pytest.mark.parametrize('mutation',[
    lambda raw: raw.update(source_tokens=[]),
    lambda raw: raw.update(target_tokens=[]),
    lambda raw: raw.pop('corrections'),
    lambda raw: raw['source_tokens'][1].update(id='s1'),
    lambda raw: raw['corrections'].update(g1={'source_start':'missing','corrections':[]}),
])
def test_actual_contract_malformed_response_is_distinct_from_alignment(mutation,key_file):
    original='Ko pridem domov bom poklical.'
    raw=response(original,original);mutation(raw)
    result,_=run(original,raw,key_file)
    assert result['status']=='malformed_response'
    assert result['execution']['response_received']and not result['execution']['neural_prediction_received']


def test_version_confidence_and_inference_are_recorded_only_if_reported(key_file):
    original='Ko pridem domov, bom poklical.'
    raw=response(original,original)
    raw.update(model_version='observed-revision',api_version='api-revision',confidence=0.8,inference_performed=False)
    result,_=run(original,raw,key_file)
    assert result['reported_model_version']=='observed-revision'
    assert result['reported_api_version']=='api-revision'and result['reported_confidence']==0.8
    assert result['execution']['inference_performed']is False
    assert result['execution']['neural_prediction_received']is True
    assert result['edits']==[]and result['proposed_text']==original


@pytest.mark.parametrize(('exception','status'),[
    (httpx.ReadTimeout('private upstream details'),'timeout'),
    (httpx.ConnectError('private upstream details'),'unavailable'),
])
def test_timeout_and_connection_failure_have_one_attempt_and_no_error_leak(key_file,exception,status):
    count=[]
    def handler(request):count.append(request);raise exception
    result=asyncio.run(check('Besedilo.',CommaCheckerConfig(key_file=key_file,transport=httpx.MockTransport(handler))))
    assert result['status']==status and len(count)==1
    assert result['execution']['invocation_attempted']and not result['execution']['response_received']
    assert 'private upstream details'not in json.dumps(result)


def test_no_redirect_retry_startup_or_unconfigured_dispatch_and_application_limit(key_file):
    count=[]
    def handler(request):count.append(request);return httpx.Response(302,headers={'Location':'https://other.example/'})
    config=CommaCheckerConfig(key_file=key_file,transport=httpx.MockTransport(handler))
    assert count==[]
    result=asyncio.run(check('Besedilo.',config))
    assert result['status']=='unavailable'and len(count)==1
    assert not result['execution']['invocation_acknowledged']
    for current in [replace(config,key_file=None),replace(config,key_file=key_file.parent/'missing')]:
        result=asyncio.run(check('Besedilo.',current))
        assert result['status']=='unavailable'and not result['execution']['invocation_attempted']
    result=asyncio.run(check('x'*3001,config))
    assert result['status']=='too_long'and result['application_limit_characters']==3000
    assert not result['truncated']and not result['execution']['invocation_attempted']


def test_malformed_json_and_response_limit_stay_local(key_file):
    for content,settings in [(b'{"source_tokens":[],"source_tokens":[]}',{}),
                            (b'{"untrusted_number":1e400}',{}),
                            (b'x'*1025,{'response_max_bytes':1024})]:
        transport=httpx.MockTransport(lambda request:httpx.Response(200,content=content))
        result=asyncio.run(check('Besedilo.',CommaCheckerConfig(key_file=key_file,transport=transport,**settings)))
        assert result['status']=='malformed_response'and not result['checked_extent']['complete']


def test_result_reopening_is_just_saved_json_not_another_provider_call(key_file):
    original='Ko pridem domov bom poklical.'
    result,requests=run(original,response(original,'Ko pridem domov, bom poklical.'),key_file)
    saved=json.dumps(result,ensure_ascii=False)
    assert json.loads(saved)==result and len(requests)==1


def test_public_reader_excludes_raw_private_fields_and_preserves_zero_width_edit(key_file):
    original='Ko pridem domov bom poklical.'
    raw=response(original,'Ko pridem domov, bom poklical.')
    raw['private_provider_note']='not public'
    observation,requests=run(original,raw,key_file)
    public=public_proposal(observation)
    assert public['original_text']==original and public['provider']=='cjvt_vejice'
    assert public['label']=='Predlog postavitve vejic · CJVT Vejice'
    assert public['edits'][0]['start']==public['edits'][0]['end']
    assert 'raw_result'not in public and 'endpoint'not in public and 'not public'not in json.dumps(public)
    assert len(requests)==1
    observation['edits'][0]['start']+=1
    with pytest.raises(ValueError):public_proposal(observation)


def test_comma_deletion_cannot_merge_original_words_or_numbers(key_file):
    for original,target in [('pride,in','pridein'),('3, 14','314')]:
        raw={'source_tokens':[{'id':'s1','text':original}],
            'target_tokens':[{'id':'t1','text':target}],
            'corrections':{'g':{'source_start':'s1','corrections':[
                {'source_id':'s1','source_text':original,'text':target}]}}}
        result,_=run(original,raw,key_file)
        assert result['status']=='alignment_failed'


def test_unhashable_provider_identity_is_a_local_malformed_response(key_file):
    original='Ko pridem domov bom poklical.'
    raw=response(original,original)
    raw['corrections']={'g':{'source_start':{},'corrections':[]}}
    result,_=run(original,raw,key_file)
    assert result['status']=='malformed_response'
