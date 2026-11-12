"""Full task operands, real freeze boundaries, no model/source endpoint calls.

The source adapter below is an explicit literal-lookup fixture. It tests the
shared document discovery path, not real database ranking or applicability.
"""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit
import re

import pytest

from pilot_backend import document_transform as doc, document_files
from pilot_backend import unified
from pilot_backend.security import Failure
from test_document_transform import family, response
from test_document_files import package

TERM = 'šolsko leto'


def placed(term, position, total=3000):
    prefix = ('x ' * (position // 2 + 1))[:max(0, position - 1)] + (' ' if position else '')
    return (prefix + term + ' ' + 'y ' * total)[:total]


class LiteralSource:
    klikai_url = 'http://127.0.0.1:1'
    editorial_full_records = False
    linghub_url = None

    def __init__(self, term=TERM, fail_at=None, saturate=False):
        self.term, self.fail_at, self.saturate, self.calls = term, fail_at, saturate, []

    def request(self, path, payload, base=None):
        self.calls.append((path, deepcopy(payload), base))
        assert payload is None
        if path == '/api/sources':
            return {'sources': [{'slug': 'sp2001', 'adapter_key': 'official_reference_jsonl',
                'display_name': 'Retained reference', 'source_type': 'reference', 'rights_mode': 'open', 'is_active': True}]}
        assert path.startswith('/api/search?')
        query = parse_qs(urlsplit(path).query)['q'][0]
        if self.fail_at and self.fail_at in query:
            raise Failure('source_timeout', 503)
        hit = {'source_slug': 'sp2001', 'resource_id': 'literal-entry', 'title': self.term,
            'rights_mode': 'open', 'snippet': 'Recorded meaning and restriction.',
            'original_text': 'Recorded meaning and restriction.', 'source_revision': 'retained-revision', 'score': 1.0}
        hits = [hit] if re.search(r'(?<!\w)' + re.escape(self.term) + r'(?!\w)', query, re.I) else []
        if self.saturate and hits:
            hits = [{**hit, 'resource_id': str(i)} for i in range(10)]
        return {'query': query, 'sources': ['sp2001'], 'hits': hits, 'count': len(hits)}


@pytest.mark.parametrize('position', [0, 490, 1480, 2488, 2980])
def test_actual_freeze_discovers_beginning_cross_window_middle_and_tail(position):
    text = placed(TERM, position)
    gateway = LiteralSource()
    bundle = doc.freeze(gateway, family('translate', text), {})['bundle']
    matches = bundle['source_bundle']['matches']
    assert len(matches) == 1 and matches[0]['external_id'] == 'literal-entry'
    plan = bundle['retrieval']['input_discovery']
    assert plan['source_sha256'] == doc.sha_text(text) and plan['complete_nonwhitespace_input']
    assert not plan['unqueried_ranges']
    a, b = text.index(TERM), text.index(TERM) + len(TERM)
    assert any(window['start'] <= a < b <= window['end'] for window in plan['windows'])
    assert all(text[window['start']:window['end']] == window['query'] for window in plan['windows'])
    assert all(0 < len(window['query']) <= 500 for window in plan['windows'])
    assert len(plan['windows']) <= doc.MAX_LOOKUP_WINDOWS
    assert all('/interpret' not in path and payload is None for path, payload, _ in gateway.calls)
    # Repeated discoveries are one full parent with original captures retained.
    assert matches[0]['capture']['original_text'] == 'Recorded meaning and restriction.'
    assert matches[0]['discovery_captures']
    assert 'discovery_captures' not in bundle['unified_evidence']['matches'][0]


def test_table_cell_operand_uses_the_same_full_discovery_path_and_global_offsets():
    text = placed(TERM, 1250, 1500)
    data = package('<w:tbl><w:tr><w:tc><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    captured = document_files.extract(data, 'table.docx')
    block = next(block for block in captured['blocks'] if TERM in block['text'])
    assert '/tc[1]/' in block['location']
    bundle = doc.freeze(LiteralSource(), family('translate', block['text']), {})['bundle']
    assert bundle['source_bundle']['matches']
    assert bundle['document_task']['source_text'] == block['text']


def test_window_boundaries_do_not_create_a_word_from_an_unrelated_substring():
    text = placed('prefix' + TERM.replace(' ', '') + 'suffix', 490)
    source = LiteralSource(term=TERM.replace(' ', ''))
    bundle = doc.freeze(source, family('translate', text), {})['bundle']
    assert bundle['source_bundle']['matches'] == []


def test_unqueryable_token_and_explicit_work_limit_keep_tail_omission_visible(monkeypatch):
    text = 'Prvi. ' + 'z' * 501 + ' ' + TERM
    plan = doc.lookup_windows(text)
    assert not plan['complete_nonwhitespace_input']
    assert plan['unqueried_ranges'] == [{'start': 6, 'end': 507, 'reason': 'token_exceeds_source_query_bound'}]
    assert any(TERM in window['query'] for window in plan['windows'])
    monkeypatch.setattr(doc, 'MAX_LOOKUP_WINDOWS', 1)
    plan = doc.lookup_windows(placed(TERM, 2500))
    assert plan['unqueried_ranges'][0]['reason'] == 'document_lookup_work_limit'
    assert not plan['complete_nonwhitespace_input']


@pytest.mark.parametrize('problem', ['timeout', 'saturated'])
def test_resource_failures_and_result_caps_are_visible_separately_from_input_coverage(problem):
    text = placed(TERM, 1400)
    gateway = LiteralSource(fail_at='y' if problem == 'timeout' else None, saturate=problem == 'saturated')
    bundle = doc.freeze(gateway, family('translate', text), {})['bundle']
    assert bundle['retrieval']['input_discovery']['complete_nonwhitespace_input']
    assert bundle['retrieval']['status'] == 'partial'
    coverage = bundle['retrieval']['coverage'][0]
    assert coverage['windows']
    if problem == 'saturated':
        assert coverage['possible_result_limit'] and coverage['has_more']
    else:
        assert any(row['status'] == 'timeout' for row in coverage['windows'])
    value = doc._attach_preflight({'status': 'failed', 'answer_md': 'Obdelava ni uspela.', 'document_result': None}, bundle)
    assert value['status'] == 'failed' and value['document_result'] is None
    assert value['limitations'][0]['code'] == 'document_source_discovery_limited'
    assert 'celotno besedilo' in value['answer_md']


def test_source_time_bound_records_unqueried_windows_without_claiming_attempts(monkeypatch):
    monkeypatch.setattr(doc, 'MAX_SOURCE_LOOKUP_SECONDS', 0)
    gateway = LiteralSource()
    bundle = doc.freeze(gateway, family('translate', placed(TERM, 2500)), {})['bundle']
    coverage = bundle['retrieval']['coverage'][0]
    assert coverage['reason']=='source_discovery_time_limit'
    assert coverage['query_variants']==[] and coverage['windows']==[]
    assert coverage['unqueried_windows'] and len(gateway.calls)==1


def test_adapter_word_limits_cover_all_six_word_phrases_without_hidden_query_prefix_loss():
    phrase='ena dva tri štiri pet šest'
    text=placed(phrase, 489)
    windows=doc.lookup_windows(text)['windows']
    assert all(window['lexical_words'] <= 12 for window in windows if window['query_kind'] in {'lexical','both'})
    assert any(phrase in window['query'] for window in windows)
    assert all(sum(range(max(0, window['lexical_words']-5), window['lexical_words']+1)) <= 64
               for window in windows if window['query_kind'] in {'lexical','both'})


def test_combining_mark_and_unrelated_substrings_cannot_become_new_word_occurrences():
    assert list(doc._surface_occurrences('c', 'čas'))==[]
    assert list(doc._surface_occurrences('na', 'banana'))==[]
    assert list(doc._surface_occurrences('DDV', '»DDV«'))==[{'start':1,'end':4,'text':'DDV'}]


def test_merge_keeps_one_complete_parent_all_nominations_raw_captures_and_competing_qualifications():
    capture={'source_slug':'solska','external_id':'entry','title':'Recorded parent','rights_mode':'open',
        'original_terms':['anno scolastico'],'standardized_terms':['šolsko leto'],'score':1.0,
        'categories':['School context only'],'matched_forms':[{'term':TERM,'matched_query':'first window'}]}
    def row(value):
        return unified.match(value,'terminology_record','School','Whole qualification',{'title':value['title']},'sl',value['score'])
    first=row(capture)
    second=row({**capture,'score':2.0,'matched_forms':[{'term':TERM,'matched_query':'second window'}]})
    competing=row({**capture,'categories':['Different geographical scope']})
    before=deepcopy([first,second,competing])
    merged=doc._merge_document_matches([first,second,competing])
    assert len(merged)==2 and [first,second,competing]==before
    combined=next(item for item in merged if len(item['capture']['matched_forms'])==2)
    assert combined['capture']['categories']==['School context only']
    assert {item['capture_sha256'] for item in combined['discovery_captures']}=={first['capture_sha256'],second['capture_sha256']}
    assert combined['capture_sha256']==doc.digest(combined['capture'])


def test_document_unigrams_reuse_existing_frozen_lexical_api_with_original_spans():
    text='Račun vključuje »DDV«.'
    capture={'term_entries':[{'lang':'sl','term':'DDV'}]}
    matches=[{'capture_kind':'terminology_record','capture':capture,'rights_mode':'open'}]
    class LexicalGateway:
        linghub_url='http://127.0.0.1:2'
        calls=[]
        def request(self,path,payload,base):
            self.calls.append((path,deepcopy(payload),base))
            assert path=='/v1/morphology/lemmatize' and payload=={'lang':'sl','tokens':['DDV'],'limit':4}
            return {'lang':'sl','status':'ok','items':[{'input':'DDV','status':'ok',
                'candidates':[{'lemma':'DDV','language':'sl','msds':['Somei'],'pos_families':['S'],'matched_by':'exact'}],
                'candidate_count':1,'ambiguous':False,'truncated':False}],
                'provenance':{'fingerprint':'recorded-release','analysis_kind':'lexical_candidates','contextual':False}}
    gateway=LexicalGateway()
    from test_document_transform import task
    result=doc._freeze_document_lexical_context(gateway,task('translate',text),matches)
    assert result['status']=='ok' and len(result['captures'])==1
    occurrence=result['requested_inputs'][0]
    assert text[occurrence['spans'][0]['start']:occurrence['spans'][0]['end']]=='DDV'
    bundle=result['captures'][0]['bundle']
    assert bundle['contract']=='klikai.pilot.morphology-lexical.v1'
    assert bundle['snapshot_sha256']==doc.digest({k:v for k,v in bundle.items() if k!='snapshot_sha256'})
    assert bundle['data']['provenance']['contextual'] is False
    assert len(gateway.calls)==1


def test_missing_lexical_resource_keeps_nomination_and_explicit_unavailable_state():
    from test_document_transform import task
    matches=[{'capture_kind':'terminology_record','capture':{'term_entries':[{'lang':'it','term':'di'}]},'rights_mode':'open'}]
    result=doc._freeze_document_lexical_context(LiteralSource(),task('translate','Libro di Maria.' )|{'source_language':'it','target_language':'sl'},matches)
    assert result['status']=='unavailable' and result['requested_inputs'][0]['text']=='di'
    assert result['captures']==[] and result['unqueried_inputs'][0]['reason']=='lexical_resource_not_configured'
