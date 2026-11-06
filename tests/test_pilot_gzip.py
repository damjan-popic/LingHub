"""Check real wire bytes on the existing authenticated API and static routes."""
import gzip
import json

import pytest
from fastapi.testclient import TestClient

from test_pilot_unified import ORIGIN, UnifiedGateway, app_for, ask, login


@pytest.fixture
def completed_family(tmp_path):
    app=app_for(tmp_path,UnifiedGateway())
    with TestClient(app,base_url=ORIGIN)as client:
        fid,family=ask(app,client,login(client))
        yield client,fid,family


def wire(client,path,encoding):
    with client.stream('GET',path,headers={'Accept-Encoding':encoding})as response:
        assert response.status_code==200
        return b''.join(response.iter_raw()),dict(response.headers)


@pytest.mark.parametrize('kind',['family','source','static'])
def test_large_real_responses_use_gzip_without_changing_the_body(completed_family,kind):
    client,fid,family=completed_family
    if kind=='family':
        path='/api/pilot/families/'+fid
    elif kind=='source':
        matches=client.get('/api/pilot/families/'+fid+'/matches').json()['items']
        item=next(row for row in matches if row['source_slug']=='jezikovna_posta')
        path='/api/pilot/families/'+fid+'/sources/'+item['evidence_id']
    else:
        path='/assets/pilot.mjs'
    plain,plain_headers=wire(client,path,'identity')
    compressed,headers=wire(client,path,'gzip')
    assert len(plain)>=1024
    assert 'content-encoding'not in plain_headers
    assert headers['content-encoding']=='gzip'
    assert 'accept-encoding'in {value.strip().lower()for value in headers['vary'].split(',')}
    assert gzip.decompress(compressed)==plain
    assert len(compressed)<len(plain)
    assert headers['content-type']==plain_headers['content-type']
    assert headers['cache-control']==plain_headers['cache-control']=='no-store'
    assert headers['x-content-type-options']=='nosniff'
    if kind!='static':assert json.loads(gzip.decompress(compressed))==json.loads(plain)


def test_small_real_job_metadata_remains_uncompressed(completed_family):
    client,_,family=completed_family
    path='/api/pilot/jobs/'+family['short']['job_id']
    plain,_=wire(client,path,'identity')
    received,headers=wire(client,path,'gzip')
    assert len(received)<1024
    assert 'content-encoding'not in headers
    assert received==plain
    assert json.loads(received)['state']=='complete'
