"""Bounded same-origin forwarding tests; no actual database or service is contacted."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import workbench_routes as wb


class WorkbenchRoutesTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(wb.router)
        self.client = TestClient(self.app)  # no resource/application startup needed
        self.addCleanup(self.client.close)
        self.app.state.workbench_slots = asyncio.Semaphore(4)
        self.calls = []

    def upstream(self, data=None, status=200, fail=None):
        @asynccontextmanager
        async def stream(method, route, **kwargs):
            self.calls.append((method, route, kwargs))
            if fail:
                raise fail
            yield httpx.Response(status, json=data, request=httpx.Request(method, 'http://127.0.0.1:8000' + route))
        self.app.state.workbench_klikai = SimpleNamespace(stream=stream)

    def test_page_csp_and_request_restricted_to_fixed_routes(self):
        page = self.client.get('/workbench/')
        self.assertEqual(page.status_code, 200)
        self.assertIn("frame-ancestors 'none'", page.headers['content-security-policy'])
        self.assertEqual(page.headers['cache-control'], 'no-store')
        self.upstream({'hits': [{'citation_id': 'fixture'}]})
        result = self.client.post('/workbench-api/terminology', json={'q': 'BES'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.calls[0][:2], ('GET', '/api/terminology/lookup'))
        self.assertEqual(self.calls[0][2]['params']['q'], 'BES')
        self.assertEqual(result.json()['result']['hits'][0]['citation_id'], 'fixture')
        rejected = self.client.post('/workbench-api/terminology', json={'q': 'BES', 'url': 'http://example.invalid/'})
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(len(self.calls), 1)

    def test_tool_forwards_original_unicode_and_rejects_oversize_before_http(self):
        self.upstream({'status': 'ok', 'trace': {'provider': 'linghub'}})
        text = '🙂 e\u0301 otrok'
        result = self.client.post('/workbench-api/tool', json={'task': 'text_check', 'text': text})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.calls[0][:2], ('POST', '/api/tools/linguistic'))
        self.assertEqual(self.calls[0][2]['json']['text'], text)
        result = self.client.post('/workbench-api/tool', json={'task': 'text_check', 'text': 'a' * 5001})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(len(self.calls), 1)

    def test_upstream_failures_do_not_return_private_details(self):
        self.upstream({'detail': '/data/private/secret'}, status=500)
        result = self.client.post('/workbench-api/terminology', json={'q': 'BES'})
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('/data/', result.text)
        self.upstream(fail=httpx.ConnectError('private upstream diagnostics'))
        result = self.client.post('/workbench-api/terminology', json={'q': 'BES'})
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('diagnostics', result.text)
        self.upstream({'large': 'x' * (4 * 1024 * 1024 + 1)})
        self.assertEqual(self.client.post('/workbench-api/terminology', json={'q': 'BES'}).status_code, 503)

    def test_external_or_ambiguous_origin_rejected_at_startup(self):
        async def attempt(origin):
            with patch.dict('os.environ', {'KLIKAI_URL': origin}):
                async with wb.lifespan(FastAPI()):
                    pass
        for origin in ('https://127.0.0.1:8000', 'http://example.invalid:8000',
                       'http://user@127.0.0.1:8000', 'http://127.0.0.1:8000/path'):
            with self.subTest(origin=origin), self.assertRaises(RuntimeError):
                asyncio.run(attempt(origin))


if __name__ == '__main__':
    unittest.main()
