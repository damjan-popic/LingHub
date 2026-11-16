"""Feature-off store compatibility and evidence-only options, offline only."""
from copy import deepcopy
import sqlite3
import pytest
from argon2 import PasswordHasher,Type
from fastapi.testclient import TestClient
from pilot_backend.api import create_app,config_checked
from pilot_backend.store import Store
from test_document_uploads import Adapter,ORIGIN,PASSWORD,login,upload,start


def config(path,**extra):
    return {'store':str(path),'allowed_origins':[ORIGIN],'secure_cookies':False,
        'unified_answers':True,'models':['auto'],'klikai_url':'http://127.0.0.1:18313',
        'document_uploads':True,'evidence_review':True,'evidence_review_only':True,**extra}


@pytest.mark.parametrize('extra',[{'document_uploads':False},{'evidence_review':False},
    {'ai_suggestions':True},{'editorial_summary':True},{'private_translation':True}])
def test_incompatible_pilot_features_reject_before_store_or_runtime(tmp_path,extra):
    with pytest.raises(ValueError):config_checked(config(tmp_path/'unused.sqlite',**extra))
    assert not(tmp_path/'unused.sqlite').exists()


@pytest.mark.parametrize('options',[{'mode':'translate','target_language':'it'},
    {'source_language':'it'},{'review_strategy':'legacy_writer'}])
def test_only_evidence_review_enters_the_owned_queue(tmp_path,options):
    adapter=Adapter();app=create_app(config(tmp_path/'fixture.sqlite'),adapter=adapter,start_workers=False,
        hasher=PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1,type=Type.ID))
    # Temporary synthetic fixture only, never a runtime/tester invitation.
    app.state.auth.create('one',PASSWORD,role='tester',origin='synthetic',must_change=False)
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client);doc=upload(client,headers,b'Nadaljni opis je bil pripravljen s pomo\xc4\x8djo tolma\xc4\x8da.')
        caps=client.get('/api/pilot/capabilities').json()
        assert caps['documents']['languages']==['sl']and caps['documents']['evidence_review_only']
        assert not caps['documents']['private_translation']and not caps['documents']['ai_suggestions']and not caps['documents']['editorial_summary']
        response=start(client,headers,doc['document_id'],**options)
        assert response.status_code==422
        with app.state.store.connect()as db:
            assert db.execute('SELECT count(*)FROM jobs').fetchone()[0]==0
            assert db.execute('SELECT count(*)FROM families').fetchone()[0]==0
        assert adapter.calls==[]


def test_ordinary_feature_off_retains_exact_existing_schema_without_document_tables(tmp_path):
    path=tmp_path/'ordinary.sqlite';store=Store(path,documents=False)
    def schema():
        with store.connect()as db:return [tuple(r)for r in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
    before=schema();assert not any(row[1]=='uploaded_documents'for row in before)
    app=create_app(config(path,document_uploads=False,evidence_review=False,evidence_review_only=False),
                   adapter=Adapter(),start_workers=False)
    assert schema()==before
    with app.state.store.tx()as db:app.state.store._expire(db,0)
    assert schema()==before
    assert app.state.config['models']==['auto']and app.state.config['unified_answers']


def test_evidence_option_selects_the_existing_strategy_without_writer_side_effect(tmp_path):
    adapter=Adapter();app=create_app(config(tmp_path/'fixture.sqlite'),adapter=adapter,start_workers=False,
        hasher=PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1,type=Type.ID))
    app.state.auth.create('one',PASSWORD,role='tester',origin='synthetic',must_change=False)
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client);doc=upload(client,headers,b'Izvirnik ostane shranjen.')
        response=start(client,headers,doc['document_id'])
        assert response.status_code==200 and response.json()['options']['review_strategy']=='evidence_review_v1'
        assert adapter.calls==[]
