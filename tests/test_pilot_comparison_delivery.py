"""Preserve scoped original-reader links through persistence and tester views."""
import pytest
from pilot_backend.jobs import render_result
from pilot_backend.security import Failure
from test_pilot_project_beta import beta_rig, ORIGIN, login, ask, finish
from fastapi.testclient import TestClient


@pytest.mark.parametrize('code',['comparison_coverage_incomplete','comparison_explanation_incomplete'])
def test_saved_partial_answer_retains_explanatory_original_without_citing_it(beta_rig,code):
    app,adapter,_,_,credentials=beta_rig
    generate=adapter.generate
    def partial(*args,**kwargs):
        result=generate(*args,**kwargs)
        result.update(status='partial',decision_sources=[{'evidence_id':'passage-1','title':'Original 1'}],
            limitations=[{'code':code,'scope':'answer','claim_ids':[],'reason':'PRIVATE_DETAIL'}])
        return result
    adapter.generate=partial
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client,credentials);started=ask(client,headers);finish(app)
        fid=started['family']['answer_family_id']
        for _ in range(2):
            result=client.get('/api/pilot/families/'+fid).json()['short']
            assert result['decision_sources']==[{'evidence_id':'passage-1','title':'Original 1'}]
            assert result['limitations']==[{'code':code,'scope':'answer','claim_ids':[]}]
            assert 'passage-1'not in {r['evidence_id']for r in result['evidence']}
        original=client.get('/api/pilot/families/'+fid+'/sources/passage-1?tier=short')
        assert original.status_code==200
        assert 'Complete original 1.'in original.json()['excerpt']


@pytest.mark.parametrize('sources',[None,'bad',[{'title':'Missing identity'}],[{'evidence_id':'x','title':'','url':'untrusted'}]])
def test_result_persistence_rejects_malformed_original_links(sources):
    with pytest.raises(Failure):render_result({'status':'partial','decision_sources':sources})
