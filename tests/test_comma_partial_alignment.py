"""Explicit provider-prefix coverage; MockTransport only, never provider/key IO."""
import asyncio
from copy import deepcopy
import unicodedata
import httpx
import pytest
from core import comma_checker as checker
from test_comma_checker import response


def observed(monkeypatch,text,raw,**settings):
    monkeypatch.setattr(checker,'_key',lambda config:'synthetic-not-a-credential')
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=raw)
    config=checker.CommaCheckerConfig(transport=httpx.MockTransport(handler),**settings)
    result=asyncio.run(checker.check(text,config))
    assert len(requests)==1
    assert result['input_text']==text and result['raw_result']==raw
    return result


def test_exact_prefix_gets_precise_edits_without_claiming_unchecked_tail(monkeypatch):
    prefix='Ko je deževalo smo ostali doma.';changed='Ko je deževalo, smo ostali doma.'
    text=prefix+'  Preostali del ostane nespremenjen.';raw=response(prefix,changed)
    result=observed(monkeypatch,text,raw)
    assert result['status']=='partial'
    assert result['reason']=='provider_partial_coverage'
    assert result['checked_extent']=={'start':0,'end':len(prefix),'offset_convention':checker.OFFSET_CONVENTION,'complete':False}
    assert result['proposed_text']==changed+text[len(prefix):]
    assert result['edits']==[{'start':prefix.index('deževalo')+len('deževalo'),'end':prefix.index('deževalo')+len('deževalo'),'original':'','replacement':',','kind':'comma_addition'}]
    assert result['truncated']is False
    assert result['execution']['neural_prediction_received']is True
    visible=checker.public_proposal(result)
    assert visible['status']=='partial'and visible['checked_extent']==result['checked_extent']
    assert 'raw_result'not in visible and 'endpoint'not in visible
    with pytest.raises(checker.AlignmentFailure):checker.align(text,raw)


@pytest.mark.parametrize('separator',['\r\n','\n','\t','\u00a0','  '])
def test_partial_coverage_preserves_all_original_boundary_whitespace(monkeypatch,separator):
    prefix='Če zmorem bom pomagal.';changed='Če zmorem, bom pomagal.'
    text=prefix+separator+'Nadaljevanje se ne preverja.'
    raw=response(prefix,changed,normalized_whitespace=True)
    result=observed(monkeypatch,text,raw)
    assert result['status']=='partial'and result['proposed_text']==changed+separator+'Nadaljevanje se ne preverja.'
    assert result['checked_extent']['end']==len(prefix)
    assert all('\r'not in e['original']+e['replacement']and'\n'not in e['original']+e['replacement']for e in result['edits'])


def test_partial_already_correct_prefix_has_no_edits_but_stays_incomplete(monkeypatch):
    prefix='Če zmorem, bom pomagal.';text=prefix+' Še nepreverjeno.'
    result=observed(monkeypatch,text,response(prefix,prefix))
    assert result['status']=='partial'and result['edits']==[]
    assert result['proposed_text']==text and not result['checked_extent']['complete']


@pytest.mark.parametrize('original,provider_source',[
    ('Prijatelj bo prišel.','Pri'),
    ('Življenje se nadaljuje.','Živ'),
    ('c\u030car ostane.','c'),
])
def test_prefix_cannot_end_inside_word_or_combining_character(monkeypatch,original,provider_source):
    result=observed(monkeypatch,original,response(provider_source,provider_source))
    assert result['status']=='alignment_failed'and result['edits']==[]and result['proposed_text']is None


def test_nonprefix_or_displaced_repeated_tokens_never_search_forward(monkeypatch):
    text='Najprej se ustavim. Če zmorem bom pomagal.'
    raw=response('Če zmorem bom pomagal.','Če zmorem, bom pomagal.')
    result=observed(monkeypatch,text,raw)
    assert result['status']=='alignment_failed'and result['edits']==[]


@pytest.mark.parametrize('source,target',[
    ('Še več ostane.','Se več ostane.'),
    ('Cena je 3,14 evra.','Cena je 314 evra.'),
    ('Poglej https://example.org/a,b.','Poglej https://example.org/ab.'),
    ('Koda `x,y` ostane.','Koda `xy` ostane.'),
])
def test_partial_prediction_does_not_relax_noncomma_numeric_or_protected_guards(monkeypatch,source,target):
    result=observed(monkeypatch,source+' Nepreverjeno.',response(source,target))
    assert result['status']=='alignment_failed'and result['edits']==[]


def test_partial_target_stream_and_competing_ids_must_still_match(monkeypatch):
    source='Če zmorem bom pomagal.';target='Če zmorem, bom pomagal.';raw=response(source,target)
    inconsistent=deepcopy(raw);inconsistent['target_tokens'][-1]['text']='drugače.'
    result=observed(monkeypatch,source+' Nepreverjeno.',inconsistent)
    assert result['status']=='alignment_failed'
    competing=deepcopy(raw);first=next(iter(competing['corrections'].values()))
    first['corrections'].append({**first['corrections'][0],'text':first['corrections'][0]['source_text']})
    result=observed(monkeypatch,source+' Nepreverjeno.',competing)
    assert result['status']=='alignment_failed'


def test_full_coverage_and_trailing_whitespace_keep_legacy_success(monkeypatch):
    source='Ko pridem bom poklical.\r\n';target='Ko pridem, bom poklical.\r\n'
    result=observed(monkeypatch,source,response(source,target,normalized_whitespace=True))
    assert result['status']=='ok'and result['proposed_text']==target
    assert result['checked_extent']['end']==len(source)and result['checked_extent']['complete']is True
    assert checker.align(source,result['raw_result'])[0]==target


def test_reader_refuses_forged_partial_extent_or_unchecked_tail_edit(monkeypatch):
    prefix='Če zmorem bom pomagal.';text=prefix+' Nepreverjeno.'
    result=observed(monkeypatch,text,response(prefix,'Če zmorem, bom pomagal.'))
    assert result['status']=='partial'
    changed=deepcopy(result);changed['checked_extent']['end']=len(text)
    with pytest.raises(ValueError):checker.public_proposal(changed)
    changed=deepcopy(result);changed['edits'].append({'start':len(text),'end':len(text),'original':'','replacement':',','kind':'comma_addition'})
    changed['proposed_text']+=','
    with pytest.raises(ValueError):checker.public_proposal(changed)


@pytest.mark.parametrize('gap',[' ','\t','\u00a0','\r\n'])
def test_untouched_provider_token_whitespace_normalization_preserves_original(monkeypatch,gap):
    text='Ko pride gremo. Opomba'+gap+'.'
    raw=response('Ko pride gremo. Opomba.','Ko pride, gremo. Opomba.')
    result=observed(monkeypatch,text,raw)
    assert result['status']=='ok'and len(result['edits'])==1
    assert result['proposed_text']=='Ko pride, gremo. Opomba'+gap+'.'
    assert result['source_alignment']=={'method':'ordered_codepoints_with_untouched_whitespace','normalized_unchanged_tokens':1}
    assert checker.public_proposal(result)['source_alignment']==result['source_alignment']
    with pytest.raises(checker.AlignmentFailure):checker.align(text,raw)


@pytest.mark.parametrize(('text','source','target'),[
    ('Ko pride gremo .','Ko pride gremo.','Ko pride gremo,.'),
    ('Ko pride gremo. Opom ba.','Ko pride gremo. Opomba.','Ko pride, gremo. Opomba.'),
    ('Ko pride gremo. Čaka .','Ko pride gremo. Caka.','Ko pride, gremo. Caka.'),
    ('Ko pride gremo. Opomba !','Ko pride gremo. Opomba.','Ko pride, gremo. Opomba.'),
])
def test_internal_whitespace_mapping_never_authorizes_changed_token_or_lexical_normalization(monkeypatch,text,source,target):
    result=observed(monkeypatch,text,response(source,target))
    assert result['status']=='alignment_failed'and result['edits']==[]and result['proposed_text']is None
