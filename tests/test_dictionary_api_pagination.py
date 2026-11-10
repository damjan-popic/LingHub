"""Authenticated public proxy → real native ASGI → compiled fixture pages.

No network, operational credentials, service startup or source/model mutation.
"""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

import candidate_app
from pilot_backend import linghub_api
from core import platform_api
from test_linghub_api_pagination import collocations,thesaurus,native_client


@pytest.fixture
def public_api(tmp_path,monkeypatch,native_client):
    secret='pagination-fixture-secret-'+'x'*32
    clients={'clients':{'pages':{'secret_sha256':hashlib.sha256(secret.encode()).hexdigest(),
        'scopes':['lexical'],'profiles':['stable'],'requests_per_minute':60}}}
    path=tmp_path/'clients.json';path.write_text(json.dumps(clients));path.chmod(0o600)
    calls=[];original_client=httpx.AsyncClient
    transport=httpx.ASGITransport(app=candidate_app.app)
    async def observe(request):
        calls.append({'path':request.url.path,'params':dict(request.url.params),
            'scope':request.headers.get('X-Linghub-Cache-Scope')})
    def client_factory(**options):
        return original_client(**options,transport=transport,event_hooks={'request':[observe]})
    monkeypatch.setattr(linghub_api.httpx,'AsyncClient',client_factory)
    app=FastAPI()
    linghub_api.install(app,{'linghub_clients_file':str(path),'linghub_url':'http://127.0.0.1:18012'})
    client=TestClient(app)
    try:yield SimpleNamespace(client=client,native=native_client,headers={'Authorization':'Bearer pages.'+secret},
        calls=calls,clients=clients,clients_path=path)
    finally:client.close()


@pytest.mark.parametrize('operation,lemma,unit,total',[('collocations','otrok','occurrence',504),('synonyms','Čas','entry',24)])
def test_public_defaults_keep_eight_records_and_add_opt_out_metadata(public_api,operation,lemma,unit,total):
    route=linghub_api.PREFIX+'/'+operation
    result=public_api.client.get(route,params={'lemma':lemma},headers=public_api.headers)
    assert result.status_code==200
    data=result.json();paging=data['pagination']
    assert paging['unit']==unit and paging['limit']==8 and paging['offset']==0
    assert paging['total']==total and paging['count_returned']==8 and paging['has_more']is True
    assert paging['next_offset']==8
    assert public_api.calls[-1]['params']['limit']=='8'
    assert public_api.calls[-1]['params']['include_pagination'].lower()=='true'
    assert public_api.calls[-1]['scope']=='api:pages'
    opted_out=public_api.client.get(route,params={'lemma':lemma,'include_pagination':'false'},headers=public_api.headers)
    assert opted_out.status_code==200 and'pagination'not in opted_out.json()
    upstream='/v1/resources/collocations'if operation=='collocations'else'/v1/resources/thesaurus'
    legacy=public_api.native.get(upstream,params={'lemma':lemma,'limit':8})
    assert opted_out.json()==legacy.json(),'Opt-out preserves the complete pre-pagination response, not merely its row count'
    assert result.headers['cache-control']=='no-store'and result.headers['x-request-id']


def test_public_collocation_limit_can_exceed_twenty_and_full_pages_have_no_overlap(public_api):
    route=linghub_api.PREFIX+'/collocations'
    first=public_api.client.get(route,params={'lemma':'otrok','limit':500},headers=public_api.headers)
    second=public_api.client.get(route,params={'lemma':'otrok','limit':500,'offset':500},headers=public_api.headers)
    assert first.status_code==second.status_code==200
    a,b=first.json(),second.json()
    assert len(a['collocations'])==500 and len(b['collocations'])==4
    assert a['pagination']['total']==b['pagination']['total']==504
    assert a['pagination']['next_offset']==500 and b['pagination']['next_offset']is None
    rows=a['collocations']+b['collocations']
    assert len({row['occurrence_id']for row in rows})==504
    assert [row['frequency']for row in rows[:3]]==[50,25,0]
    assert all(row['frequency']is None and row['logDice']is None for row in rows[3:])
    assert all(row['sense_id']=='16268462'and row['headword_lexical_unit_id']=='90'for row in rows)
    assert all(row['components'][0]['msd']=='Agpmpn'for row in rows)


def test_public_synonym_pages_count_source_entries_not_returned_relation_words(public_api):
    route=linghub_api.PREFIX+'/synonyms'
    first=public_api.client.get(route,params={'lemma':'Čas','limit':20},headers=public_api.headers)
    second=public_api.client.get(route,params={'lemma':'Čas','limit':20,'offset':20},headers=public_api.headers)
    assert first.status_code==second.status_code==200
    a,b=first.json(),second.json()
    assert [len(a['entries']),len(b['entries'])]==[20,4]
    assert len({entry['entry_order']for entry in a['entries']+b['entries']})==24
    assert a['pagination']['returned_relations']>20
    assert a['pagination']['truncated_within_entries']is True
    assert b['pagination']['truncated_within_entries']is False
    assert a['count_relations']==b['count_relations']and a['count_senses']==b['count_senses']
    assert a['entries'][0]['lexical_units'][0]['lexical_unit_id']=='001'
    assert all(entry['headword']=='Čas'for entry in a['entries']+b['entries'])


def test_public_filtered_and_past_end_pages_use_filtered_total(public_api):
    route=linghub_api.PREFIX+'/collocations'
    first=public_api.client.get(route,params={'lemma':'otrok','limit':1,'min_freq':25},headers=public_api.headers)
    assert first.status_code==200
    page=first.json()['pagination'];assert page['total']==2 and page['source_total']==504
    empty=public_api.client.get(route,params={'lemma':'otrok','offset':2,'min_freq':25},headers=public_api.headers)
    assert empty.status_code==200 and empty.json()['collocations']==[]
    assert empty.json()['pagination']['has_more']is False
    maximum=public_api.client.get(route,params={'lemma':'otrok','offset':2147483647},headers=public_api.headers)
    assert maximum.status_code==200 and maximum.json()['collocations']==[]
    assert maximum.json()['pagination']['offset']==2147483647
    absent=public_api.client.get(route,params={'lemma':'unrecorded-fixture'},headers=public_api.headers)
    assert absent.status_code==200 and absent.json()['pagination']['total']==0


@pytest.mark.parametrize('operation,params',[
    ('collocations',{'limit':501}),('synonyms',{'limit':21}),
    ('collocations',{'limit':0}),('synonyms',{'limit':0}),
    ('collocations',{'offset':-1}),('synonyms',{'offset':-1}),
    ('collocations',{'offset':2147483648}),('synonyms',{'offset':2147483648}),
    ('collocations',{'offset':'1.5'}),('synonyms',{'offset':'1.5'}),
    ('collocations',{'include_pagination':'not-a-bool'}),('synonyms',{'include_pagination':'not-a-bool'})])
def test_public_bounds_reject_invalid_parameters(public_api,operation,params):
    result=public_api.client.get(linghub_api.PREFIX+'/'+operation,params={'lemma':'otrok',**params},headers=public_api.headers)
    assert result.status_code==422
    # Record/offset limits belong to the proxy; native FastAPI already owns
    # boolean query parsing, so malformed booleans need no duplicate guard.
    if'include_pagination'not in params:assert public_api.calls==[]


@pytest.mark.parametrize('operation',['collocations','synonyms'])
def test_public_native_missing_or_empty_lemma_errors_remain(public_api,operation):
    route=linghub_api.PREFIX+'/'+operation
    assert public_api.client.get(route,headers=public_api.headers).status_code==422
    assert public_api.client.get(route,params={'lemma':'  '},headers=public_api.headers).status_code==400


def test_paging_does_not_relax_bearer_scopes_revocation_or_closed_routes(public_api):
    route=linghub_api.PREFIX+'/collocations'
    assert public_api.client.get(route,params={'lemma':'otrok','limit':500}).status_code==401
    assert public_api.client.get(route,params={'lemma':'otrok'},headers={'Authorization':'Bearer pages.invalid'}).status_code==401
    assert public_api.client.get(linghub_api.PREFIX+'/internal/models',headers=public_api.headers).status_code==404
    public_api.clients['clients']['pages']['scopes']=['read']
    public_api.clients_path.write_text(json.dumps(public_api.clients))
    assert public_api.client.get(route,params={'lemma':'otrok'},headers=public_api.headers).status_code==403
    public_api.clients['clients']['pages']['revoked']=True
    public_api.clients_path.write_text(json.dumps(public_api.clients))
    assert public_api.client.get(route,params={'lemma':'otrok'},headers=public_api.headers).status_code==401
    assert public_api.calls==[]


def test_public_capabilities_separate_dictionary_limits_from_native_explore_limit(public_api,monkeypatch):
    public_api.clients['clients']['pages']['scopes'].append('read')
    public_api.clients_path.write_text(json.dumps(public_api.clients))
    monkeypatch.setattr(platform_api,'analyzer_request',AsyncMock(side_effect=platform_api.AnalyzerFailure('unavailable','fixture')))
    candidate_app.app.state.lexical_classla_url=None  # native_client restores the complete state.
    result=public_api.client.get(linghub_api.PREFIX+'/capabilities',headers=public_api.headers)
    assert result.status_code==200
    data=result.json()
    assert data['limits']['dictionary_records_per_lemma']==20  # Existing explore bound is independent.
    assert data['application_api']['dictionary_pagination']=={
        'synonyms':{'default_limit':8,'max_limit':20,'unit':'entry','max_offset':2147483647,'include_pagination_default':True},
        'collocations':{'default_limit':8,'max_limit':500,'unit':'occurrence','max_offset':2147483647,'include_pagination_default':True}}
