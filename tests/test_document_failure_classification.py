"""Twenty bounded synthetic controls; exact AST owners, no app imports/HTTP/DB."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
SENTINEL='SYNTHETIC_SOURCE_PASSWORD_SENTINEL'
DOCUMENT='/internal/pilot/document-transform'


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def owners():
    ns={'json':json,'re':re,'Path':Path,'canonical':canonical,'digest':digest}
    tree=ast.parse((ROOT/'pilot_backend/security.py').read_text())
    failure=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Failure')
    exec(compile(ast.Module(body=[failure],type_ignores=[]),'exact_failure_contract','exec'),ns)
    tree=ast.parse((ROOT/'pilot_backend/adapter.py').read_text())
    gateway=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Gateway')
    request=next(n for n in gateway.body if isinstance(n,ast.FunctionDef) and n.name=='request')
    exec(compile(ast.Module(body=[request],type_ignores=[]),'exact_gateway_request','exec'),ns)
    tree=ast.parse((ROOT/'pilot_backend/human_feedback.py').read_text())
    names={'safe_exception','failure_display','terminal_display','visible_result'}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'exact_feedback_owners','exec'),ns)
    return ns


NS=owners()


class FakeHTTPError(Exception):pass
class FakeTimeout(FakeHTTPError):pass


def response_call(status,body,path=DOCUMENT,read_error=False):
    chunks=body if isinstance(body,list) else [body]
    class Response:
        status_code=status
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def iter_bytes(self):
            if read_error:raise FakeHTTPError(SENTINEL)
            yield from chunks
    class Client:
        def __init__(self,**kwargs):
            assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def stream(self,*args,**kwargs):return Response()
    NS['httpx']=SimpleNamespace(Client=Client,Timeout=lambda *args,**kwargs:None,
        TimeoutException=FakeTimeout,HTTPError=FakeHTTPError)
    gateway=SimpleNamespace(url='http://127.0.0.1:1',token='synthetic-token',
        editorial_operator_policy_id=None,klikai_url=None)
    try:return NS['request'](gateway,path,{'synthetic':'input'})
    except NS['Failure'] as error:return error


def error_body(code,extra=None):
    return json.dumps({'detail':{'code':code,**(extra or{})}}).encode()


class DocumentFailureClassification(unittest.TestCase):
    def test_01_known_busy503_retains_safe_code_and_status(self):
        e=response_call(503,error_body('PILOT_BUSY',{'message':SENTINEL}))
        self.assertEqual((e.code,e.http_status),('PILOT_BUSY',503))
        self.assertNotIn(SENTINEL,json.dumps(e.__dict__))

    def test_02_known_timeout504_retains_explicit_code_and_actual_status(self):
        e=response_call(504,error_body('PILOT_TIMEOUT'))
        self.assertEqual((e.code,e.http_status),('PILOT_TIMEOUT',504))

    def test_03_known_invalid_document422_is_not_relabelled_source_absence(self):
        e=response_call(422,b'{"detail":"PILOT_INVALID_DOCUMENT_REQUEST"}')
        self.assertEqual((e.code,e.http_status),('PILOT_INVALID_DOCUMENT_REQUEST',422))

    def test_04_unknown500_never_retains_body_or_arbitrary_code(self):
        e=response_call(500,error_body(SENTINEL,{'message':SENTINEL}))
        self.assertEqual((e.code,e.http_status),('answer_unavailable',500))
        self.assertNotIn(SENTINEL,json.dumps(e.__dict__))

    def test_05_unknown503_is_not_inferred_busy(self):
        e=response_call(503,error_body('UNRECOGNIZED_PROVIDER_STATE'))
        self.assertEqual((e.code,e.http_status),('answer_unavailable',503))

    def test_06_malformed500_is_safe_and_keeps_numeric_status(self):
        e=response_call(500,b'not-json '+SENTINEL.encode())
        self.assertEqual((e.code,e.http_status),('answer_unavailable',500))
        self.assertNotIn(SENTINEL,json.dumps(e.__dict__))

    def test_07_oversized503_with_known_prefix_is_not_admitted(self):
        body=json.dumps({'detail':'PILOT_BUSY','private':SENTINEL+'x'*1100}).encode()
        e=response_call(503,[body[:500],body[500:]])
        self.assertEqual((e.code,e.http_status),('answer_unavailable',503))
        self.assertNotIn(SENTINEL,json.dumps(e.__dict__))

    def test_08_validation_detail_list422_does_not_export_inputs(self):
        e=response_call(422,json.dumps({'detail':[{'input':SENTINEL}]}).encode())
        self.assertEqual((e.code,e.http_status),('answer_unavailable',422))
        self.assertNotIn(SENTINEL,json.dumps(e.__dict__))

    def test_09_success200_body_is_unchanged(self):
        body={'status':'ok','native':{'preserved':True}}
        self.assertEqual(response_call(200,json.dumps(body).encode()),body)

    def test_10_unrelated_endpoint_failure_stays_legacy(self):
        e=response_call(500,error_body('PILOT_BUSY'),path='/internal/unrelated')
        self.assertEqual(e.code,'answer_unavailable');self.assertFalse(hasattr(e,'http_status'))

    def test_11_safe_exception_adds_numeric_http_without_exception_text(self):
        e=NS['Failure']('PILOT_BUSY',503);e.http_status=503
        value=NS['safe_exception'](e)
        self.assertEqual(value['http_status'],503);self.assertEqual(value['error_code'],'PILOT_BUSY')
        self.assertNotIn('message',value)

    def test_12_boolean_http_status_is_rejected(self):
        e=NS['Failure']('answer_unavailable');e.http_status=True
        self.assertNotIn('http_status',NS['safe_exception'](e))

    def test_13_string_http_status_and_message_are_not_retained(self):
        e=RuntimeError(SENTINEL);e.http_status=SENTINEL
        value=NS['safe_exception'](e)
        self.assertNotIn('http_status',value);self.assertNotIn(SENTINEL,json.dumps(value))

    def test_14_known_busy_has_localized_slovene_retry_text(self):
        value=NS['failure_display']('failed','sl','PILOT_BUSY')
        self.assertIn('zasedena',value['short_answer'])
        self.assertIn('znova',value['short_answer'])

    def test_15_known_timeout_has_localized_italian_retry_text(self):
        value=NS['failure_display']('failed','it','PILOT_TIMEOUT')
        self.assertIn('tempo',value['short_answer']);self.assertIn('Riprova',value['short_answer'])

    def test_16_cancelled_display_ignores_old_busy_code(self):
        value=NS['failure_display']('cancelled','sl','PILOT_BUSY')
        self.assertEqual(value['short_answer'],'Zahteva je preklicana.')

    def test_17_saved_failed_display_is_returned_without_retrofit(self):
        old={'status':'failed','blocks':[{'text':'Saved original failure wording'}],
            'short_answer':'Saved original failure wording'}
        before=deepcopy(old)
        self.assertEqual(NS['visible_result']({'state':'failed','result':json.dumps(old)},'it'),before)

    def test_18_unknown_code_has_exact_legacy_failure_payload(self):
        original={'status':'failed','answer_kind':'failed',
            'answer_md':'Non è stato possibile preparare la risposta. Riprova.',
            'short_answer':'Non è stato possibile preparare la risposta. Riprova.',
            'blocks':[{'block_id':'attempt-status','text':'Non è stato possibile preparare la risposta. Riprova.'}],
            'evidence':[],'citations':[],'source_badges':[]}
        self.assertEqual(NS['failure_display']('failed','it',SENTINEL),original)

    def test_19_terminal_display_reads_safe_job_code_not_exception_text(self):
        calls=[]
        db=SimpleNamespace(execute=lambda sql,params:(calls.append((sql,params)) or
            SimpleNamespace(fetchone=lambda:('it','PILOT_TIMEOUT'))))
        raw,display_hash=NS['terminal_display'](db,'synthetic-run','failed')
        self.assertIn('jobs',calls[0][0]);self.assertIn('tempo',json.loads(raw)['short_answer'])
        self.assertEqual(display_hash,digest(json.loads(raw)['blocks']))

    def test_20_non200_error_body_transport_failure_keeps_known_status_safely(self):
        e=response_call(500,b'',read_error=True)
        self.assertEqual((e.code,e.http_status),('answer_unavailable',500))
        self.assertNotIn(SENTINEL,json.dumps(e.__dict__))


if __name__=='__main__':unittest.main(verbosity=2)
