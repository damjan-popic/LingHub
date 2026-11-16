"""Offline complete-record bounds across document discovery windows.

The dense adapter is a mechanical protocol fixture, not a linguistic assertion.
"""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit
import hashlib

import pytest

from pilot_backend import document_transform as doc, unified
from pilot_backend.security import Failure
from test_document_transform import family


def definition(slug, adapter='official_reference_jsonl', source_type='reference'):
    return {'slug':slug, 'adapter_key':adapter, 'display_name':'Source '+slug,
            'source_type':source_type, 'rights_mode':'open', 'is_active':True}


class DenseSource:
    klikai_url='http://127.0.0.1:1'
    linghub_url=None
    editorial_full_records=False

    def __init__(self, definitions=None, per_response=10, fail=False):
        self.definitions=definitions or [definition('reference-'+str(i)) for i in range(3)]
        self.per_response=per_response
        self.fail=fail
        self.calls=[]
        self.returned=[]

    def request(self, path, payload, base=None):
        assert payload is None
        if path=='/api/sources':
            return {'sources':deepcopy(self.definitions)}
        assert path.startswith('/api/search?')
        params=parse_qs(urlsplit(path).query)
        query,slug,limit=params['q'][0],params['sources'][0],int(params['limit'][0])
        self.calls.append({'query':query,'source':slug,'limit':limit})
        if self.fail:
            raise Failure('source_timeout',503)
        marker=hashlib.sha256(query.encode()).hexdigest()[:12]
        hits=[{'source_slug':slug,'resource_id':marker+'-'+str(i),'title':'Complete source '+str(i),
            'rights_mode':'open','source_revision':'recorded-'+marker,
            'snippet':'A discovery snippet only.',
            'original_text':query+' — Complete governing condition; exception and competing scope remain together.',
            'normative_status':'proposed-needs-review','score':1.0}
            for i in range(min(self.per_response,limit))]
        self.returned.extend(deepcopy(hits))
        return {'query':query,'sources':[slug],'hits':hits,'count':len(hits)}


def long_operand():
    # Many distinct literal and lexical windows, within the existing task limit.
    return ' '.join('beseda'+str(i) for i in range(190))


def test_dense_multisource_discovery_keeps_all_returned_whole_records_and_marks_unqueried_windows():
    source=DenseSource()
    text=long_operand()
    value=doc.freeze(source,family('translate',text),{})
    bundle=value['bundle'];doc.checked(bundle)
    matches=bundle['source_bundle']['matches']
    assert len(doc.lookup_windows(text)['windows'])>12
    assert len(matches)==30 and len(source.returned)==30
    assert {(r['source_slug'],r['external_id']) for r in matches}=={(r['source_slug'],r['resource_id']) for r in source.returned}
    by_key={(r['source_slug'],r['resource_id']):r for r in source.returned}
    for row in matches:
        original=by_key[(row['source_slug'],row['external_id'])]
        assert row['capture']==original
        assert original['original_text'] in row['source']['excerpt']
        assert row['source']['bibliography']['normative_status']=='proposed-needs-review'
        assert row['discovery_captures'][0]['capture']==original
    assert bundle['retrieval']['status']=='partial'
    assert bundle['retrieval']['input_discovery']['complete_nonwhitespace_input']
    for row in bundle['retrieval']['coverage']:
        assert row['record_limit']==10 and row['returned_count']==10
        assert row['unqueried_windows'] and row['status']=='partial'
        assert all(w['reason']=='document_source_record_limit' for w in row['unqueried_windows'])
        assert len(row['windows'])==1
    assert all(call['limit']==10 for call in source.calls)
    # Every retrieved complete record can still be opened through its captured identity.
    for row in matches:
        reader=unified.source(bundle['source_bundle'],row['evidence_id'])
        assert row['capture']['original_text'] in reader['excerpt']


def test_remaining_source_budget_is_sent_to_adapter_without_discarding_returned_records():
    source=DenseSource([definition('reference')],per_response=3)
    bundle=doc.freeze(source,family('translate',long_operand()),{})['bundle']
    assert [row['limit'] for row in source.calls]==[10,7,4,1]
    assert len(source.returned)==len(bundle['source_bundle']['matches'])==10
    coverage=bundle['retrieval']['coverage'][0]
    assert coverage['returned_count']==10 and coverage['unqueried_windows']
    assert coverage['limit']==22 # Actual four HTTP result bounds; no fictitious attempted windows.


def test_all_sources_share_capture_capacity_before_concurrent_queries_and_reserve_complete_headings():
    sources=[definition('term-'+str(i),'luka_jsonl_terminology','terminology') for i in range(30)]
    sources+=[definition('sp2001'),definition('nlp','linghub','tool')]
    limits=doc._document_source_record_limits(sources,{'mode':'review'})
    assert limits['nlp']==0
    assert sum(limits.values())+20==256
    assert all(0<limits[row['slug']]<=unified.source_result_limit(row) for row in sources if row['slug']!='nlp')
    # The reservation covers every result of both existing ten-record heading lookups,
    # including distinct revisions with identical source heading text.
    source=DenseSource([definition('sp2001'),definition('reference-a'),definition('reference-b')])
    task_family=family('review',long_operand())
    task_family['request_options']['document_task']['review_strategy']='evidence_review_v1'
    bundle=doc.freeze(source,task_family,{})['bundle'];doc.checked(bundle)
    assert len(bundle['source_bundle']['matches'])==50
    headings=[call for call in source.calls if call['query'] in doc.REVIEW_REFERENCE_HEADINGS]
    assert len(headings)==2 and all(call['limit']==10 for call in headings)
    assert all(sum(heading in row['capture']['original_text'] for row in bundle['source_bundle']['matches'])==10
               for heading in doc.REVIEW_REFERENCE_HEADINGS)


@pytest.mark.parametrize('problem',['unavailable','not_documentary'])
def test_source_failure_and_non_documentary_controls_keep_truthful_scope(problem):
    sources=[definition('reference')] if problem=='unavailable' else [definition('nlp','linghub','tool')]
    source=DenseSource(sources,fail=problem=='unavailable')
    bundle=doc.freeze(source,family('translate',long_operand()),{})['bundle']
    assert bundle['source_bundle']['matches']==[]
    coverage=bundle['retrieval']['coverage'][0]
    if problem=='unavailable':
        assert coverage['status']=='timeout' and coverage['unqueried_windows']
        assert len(source.calls)==1
    else:
        assert coverage['status']=='not_documentary' and source.calls==[]


def test_short_document_and_ordinary_source_defaults_keep_their_native_complete_result():
    source=DenseSource([definition('reference')],per_response=1)
    text='Nadaljni načrt sodelovanja.'
    bundle=doc.freeze(source,family('translate',text),{})['bundle']
    assert bundle['document_task']['source_text']==text
    assert len(bundle['source_bundle']['matches'])==1
    assert bundle['retrieval']['status']=='complete'
    assert bundle['retrieval']['coverage'][0]['unqueried_windows']==[]
    assert source.calls==[{'query':text,'source':'reference','limit':10}]
    for kind in [definition('term','luka_jsonl_terminology'),definition('jezikovna-svetovalnica'),definition('reference')]:
        assert unified.source_result_limit(kind) in {3,10,20}
    ordinary=DenseSource([definition('reference')],per_response=1)
    records,status=unified.collect_source(ordinary,{'question':text,'locale':'sl'},definition('reference'))
    assert len(records)==1 and status['limit']==10
    assert ordinary.calls==[{'query':text,'source':'reference','limit':10}]


def test_short_title_and_table_operands_keep_original_text_locations_and_complete_sources():
    from pilot_backend import document_files
    from test_document_files import package
    title='Nadaljni načrt sodelovanja'
    table='Besedilo je bilo pripravljeno s pomočjo tolmača.'
    original=package('<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>'+title+
        '</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>'+table+
        '</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    captured=document_files.extract(original,'title-table.docx')
    assert [block['text'] for block in captured['blocks']]==[title,table]
    for block in captured['blocks']:
        source=DenseSource([definition('reference')],per_response=1)
        f=family('review',block['text'])
        f['request_options']['document_task'].update(review_strategy='evidence_review_v1',
            document_revision=captured['sha256'],document_location=block['location'])
        bundle=doc.freeze(source,f,{})['bundle'];doc.checked(bundle)
        assert bundle['document_task']['source_text']==block['text']
        assert bundle['document_task']['document_location']==block['location']
        assert bundle['retrieval']['status']=='complete'
        assert len(bundle['source_bundle']['matches'])==1


@pytest.mark.parametrize('bad',[0,-1,11,True,'2'])
def test_optional_source_override_rejects_invalid_or_wider_limits(bad):
    source=DenseSource([definition('reference')],per_response=1)
    with pytest.raises(Failure) as failure:
        unified.collect_source(source,{'question':'primer','locale':'sl'},definition('reference'),result_limit=bad)
    assert failure.value.code=='invalid_request' and source.calls==[]
