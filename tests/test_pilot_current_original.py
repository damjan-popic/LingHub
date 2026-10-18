"""Explicit current originals are owner-scoped additions, never frozen citations."""
from copy import deepcopy
import json
from urllib.parse import quote

from fastapi.testclient import TestClient
import pytest

from pilot_backend import unified
from pilot_backend.presentation import tester_view as reader_view
from pilot_backend.security import Failure
from pilot_backend.store import digest
from test_pilot_unified import UnifiedGateway,app_for,ask,login,ORIGIN


class UnavailableDetailGateway(UnifiedGateway):
    def __init__(self):
        super().__init__(provider_failure=True)
        self.detail_ready=False
        self.detail_calls=[]

    def request(self,path,payload,base=None):
        if path.startswith('/api/editorial/records/'):
            self.detail_calls.append(path)
            if not self.detail_ready:raise Failure('source_timeout',503)
        result=super().request(path,payload,base)
        if path=='/internal/pilot/freeze':
            # Like the real gateway, retain full discovery captures privately;
            # only their hashes/identities appear in the public match projection.
            result['bundle']['payload']={'unified_evidence':deepcopy(payload['unified_evidence'])}
            for row in result['matches']:row.pop('capture_kind',None)
        return result


def prepare(tmp_path):
    gateway=UnavailableDetailGateway();app=app_for(tmp_path,gateway)
    return gateway,app


def frozen_rows(app,fid):
    with app.state.store.connect()as db:
        return (db.execute('SELECT bundle FROM families WHERE id=?',(fid,)).fetchone()[0],
            [dict(row)for row in db.execute('SELECT *FROM runs WHERE family_id=?',(fid,))])


def original_route(client,fid):
    items=client.get('/api/pilot/families/'+fid+'/matches').json()['items']
    item=next(row for row in items if row['source_slug']=='jezikovna_posta')
    return '/api/pilot/families/'+fid+'/sources/'+quote(item['evidence_id'],safe='')


def test_current_original_is_explicit_and_preserves_answer_sources_and_feedback(tmp_path):
    gateway,app=prepare(tmp_path)
    with TestClient(app,base_url=ORIGIN)as client:
        fid,family=ask(app,client,login(client))
        path=original_route(client,fid);before=frozen_rows(app,fid)
        calls=len(gateway.detail_calls)
        historical=client.get(path).json()
        assert len(gateway.detail_calls)==calls

        assert historical['original_view']=='historical_unavailable'
        assert historical['current_original_lookup']is True
        assert 'Faithful original answer'not in historical['excerpt']
        # These reader fields also survive the project tester boundary.
        projected=reader_view(historical,'fixture-owner','fixture-secret')
        assert projected['original_view']=='historical_unavailable'
        assert projected['current_original_lookup']is True
        gateway.detail_ready=True
        response=client.get(path+'/current-original')
        assert response.status_code==200,response.text
        current=response.json()
        assert current['original_view']=='supplementary_current'
        assert current['evidence_id']==historical['evidence_id']
        assert gateway.record['answer_text']in current['excerpt']
        assert gateway.record['question_original']in current['excerpt']
        assert len(gateway.detail_calls)==calls+1
        assert client.get(path).json()==historical
        assert client.get('/api/pilot/families/'+fid).json()==family
        assert frozen_rows(app,fid)==before
        gateway.record.update(answer_text='A newly available current edition.',content_hash='b'*64)
        later=client.get(path+'/current-original').json()
        assert 'A newly available current edition.'in later['excerpt']
        assert current['excerpt']!=later['excerpt']
        assert client.get(path).json()==historical and frozen_rows(app,fid)==before


def test_current_original_owner_evidence_tier_and_auth_boundaries_precede_upstream(tmp_path):
    gateway,app=prepare(tmp_path)
    with TestClient(app,base_url=ORIGIN)as client:
        fid,_=ask(app,client,login(client));path=original_route(client,fid)
        gateway.detail_ready=True;calls=len(gateway.detail_calls)
        assert client.get(path+'/current-original?tier=invalid').status_code==400
        assert client.get(path+'/current-original?tier=long').status_code==404
        assert client.get('/api/pilot/families/'+fid+'/sources/guessed/current-original').status_code==404
        ordinary=next(row for row in client.get('/api/pilot/families/'+fid+'/matches').json()['items']if row['source_slug']=='solska')
        assert client.get('/api/pilot/families/'+fid+'/sources/'+quote(ordinary['evidence_id'],safe='')+'/current-original').status_code==404
        login(client,'unified-other')
        assert client.get(path+'/current-original').status_code==404
        with TestClient(app,base_url=ORIGIN)as anonymous:
            assert anonymous.get(path+'/current-original').status_code==401
        assert len(gateway.detail_calls)==calls


@pytest.mark.parametrize('change',[
    {'resource_id':'another-resource'}, {'source_slug':'jezikovna_urgenca'},
    {'rights_mode':'linked_only'}, {'access_policy':None}, {'content_hash':'bad'},
])
def test_current_record_identity_and_existing_access_contract_are_enforced(tmp_path,change):
    gateway,app=prepare(tmp_path)
    with TestClient(app,base_url=ORIGIN)as client:
        fid,_=ask(app,client,login(client));path=original_route(client,fid)
        historical=client.get(path).json();before=frozen_rows(app,fid)
        gateway.detail_ready=True;gateway.record.update(change)
        # The fixture transport ordinarily checks resource identity itself.
        original=gateway.request
        def invalid_record(route,payload,base=None):
            return deepcopy(gateway.record)if route.startswith('/api/editorial/records/')else original(route,payload,base)
        gateway.request=invalid_record
        response=client.get(path+'/current-original')
        assert response.status_code==502
        assert 'Faithful original answer'not in response.text
        assert client.get(path).json()==historical and frozen_rows(app,fid)==before


def test_unavailable_current_original_keeps_historical_read_and_state(tmp_path):
    gateway,app=prepare(tmp_path)
    with TestClient(app,base_url=ORIGIN)as client:
        fid,_=ask(app,client,login(client));path=original_route(client,fid)
        historical=client.get(path).json();before=frozen_rows(app,fid)
        assert client.get(path+'/current-original').status_code==503
        assert client.get(path).json()==historical and frozen_rows(app,fid)==before


def test_no_unpinned_or_browser_selected_current_identity_is_accepted(tmp_path):
    gateway,app=prepare(tmp_path)
    with TestClient(app,base_url=ORIGIN)as client:
        fid,_=ask(app,client,login(client))
        bundle=json.loads(frozen_rows(app,fid)[0])
        row=next(row for row in bundle['matches']if row['source_slug']=='jezikovna_posta')
        eid=row['evidence_id'];calls=len(gateway.detail_calls)
        for mutate in ('capture','identity','kind','status'):
            changed=deepcopy(bundle)
            chosen=next(row for row in changed['matches']if row['evidence_id']==eid)
            if mutate=='capture':
                capture=next(row['capture']for row in changed['gateway_bundle']['payload']['unified_evidence']['matches']if row['source_slug']=='jezikovna_posta')
                capture['resource_id']='browser-selected-other-record'
            elif mutate=='identity':chosen['external_id']='browser-selected-other-record'
            elif mutate=='kind':chosen['capture_kind']='editorial_record'
            else:chosen['detail_status']='ok'
            changed['snapshot_sha256']=digest({k:v for k,v in changed.items()if k!='snapshot_sha256'})
            with pytest.raises(Failure)as error:unified.current_original(gateway,changed,eid)
            assert error.value.status==404
        assert len(gateway.detail_calls)==calls


def test_actual_failed_posta_capture_opens_separate_validated_current_record():
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from pilot_backend import editorial
    frozen=os.environ.get('KLIKAI_CURRENT_ORIGINAL_FROZEN')
    current=os.environ.get('KLIKAI_CURRENT_ORIGINAL_RECORD')
    if not frozen or not current:pytest.skip('Set retained original captures for actual Pošta replay')
    bundle=json.loads(Path(frozen).read_text())['bundle'];before=deepcopy(bundle)
    record=json.loads(Path(current).read_text())
    item=next(row for row in bundle['matches']if row.get('external_id')==record['resource_id'])
    assert item['detail_status']=='unavailable'and 'capture_kind'not in item
    paths=[]
    def request(path,payload,base):
        assert payload is None;paths.append(path);return deepcopy(record)
    gateway=SimpleNamespace(klikai_url='http://127.0.0.1:8000',editorial_full_records=True,
        editorial_operator_policy_id=editorial.OPERATOR_POLICY_ID,request=request)
    historical=unified.source(bundle,item['evidence_id'])
    current=unified.current_original(gateway,bundle,item['evidence_id'])
    assert historical['excerpt']==item['source']['excerpt']
    assert current['original_view']=='supplementary_current'
    assert record['question_original']in current['excerpt']and record['answer_text']in current['excerpt']
    assert paths==['/api/editorial/records/'+record['source_slug']+'/'+quote(record['resource_id'],safe='')]
    assert reader_view(current,'fixture','fixture')['excerpt']==current['excerpt']
    assert bundle==before
