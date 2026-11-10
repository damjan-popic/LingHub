"""Optional precomputed presentation data must not become an answer dependency."""
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
import pytest

from pilot_backend.api import create_app
from pilot_backend import frequency_position
from test_pilot_unified import ORIGIN, UnifiedGateway


def application(tmp_path,reference):
    return create_app({'store':str(tmp_path/'private/pilot.sqlite'),
        'allowed_origins':[ORIGIN],'secure_cookies':False,
        'unified_answers':True,'klikai_url':'http://127.0.0.1:8000',
        'frequency_position_reference':str(reference)},adapter=UnifiedGateway(),
        hasher=PasswordHasher(time_cost=1,memory_cost=8,parallelism=1),start_workers=False)


def test_reference_is_loaded_once_per_app_not_per_read(tmp_path,monkeypatch):
    calls=[];reference=object()
    def load(path):
        calls.append(path)
        return reference
    monkeypatch.setattr(frequency_position,'Reference',load)
    path=tmp_path/'frequency/manifest.json'
    app=application(tmp_path,path)
    assert app.state.domain.frequency_reference is reference
    with TestClient(app,base_url=ORIGIN)as client:
        for _ in range(3):
            assert client.get('/health').status_code==200
            assert client.get('/api/pilot/session').status_code==200
    assert calls==[path]


@pytest.mark.parametrize('error',[FileNotFoundError,ValueError])
def test_unavailable_optional_reference_keeps_the_app_available(tmp_path,monkeypatch,error):
    def unavailable(path):raise error('Synthetic unavailable reference')
    monkeypatch.setattr(frequency_position,'Reference',unavailable)
    app=application(tmp_path,tmp_path/'absent.json')
    assert app.state.domain.frequency_reference is None
    with TestClient(app,base_url=ORIGIN)as client:
        assert client.get('/health').json()['status']=='ok'
