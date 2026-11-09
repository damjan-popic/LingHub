"""The real static route must serve the UI's actual import graph."""
import re

from fastapi.testclient import TestClient

from test_pilot_unified import ORIGIN, UnifiedGateway, app_for


def test_entrypoint_imports_are_available_through_the_real_asset_route(tmp_path):
    app=app_for(tmp_path,UnifiedGateway())
    with TestClient(app,base_url=ORIGIN)as client:
        pending=['pilot.mjs'];seen=set()
        while pending:
            name=pending.pop()
            if name in seen:continue
            seen.add(name)
            response=client.get('/assets/'+name)
            assert response.status_code==200,name
            assert response.headers['content-type'].split(';')[0]in {'text/javascript','application/javascript'}
            assert response.headers['cache-control']=='no-store'
            pending.extend(re.findall(r"(?:from\s*|import\s*)['\"]\./([^'\"]+\.mjs)['\"]",response.text))
        assert 'answer-layout.mjs'in seen
        assert client.get('/assets/not-a-published-module.mjs').status_code==404
