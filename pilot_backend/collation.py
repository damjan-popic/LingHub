"""Explicit task/focus routing over fixed, read-only CPU interfaces.

Tool names are operational facets, never ontology assignments. No question IDs,
model dispatcher, guessed text language or cross-resource sense joins occur here.
"""
from __future__ import annotations
import copy
import re
import time
from urllib.parse import urlencode
from .store import digest, canonical
from .security import Failure
from . import terminology

VERSION = 'klikai.tool-collation.v1'
MAX_BYTES = 128 * 1024
_PATTERNS = {
    'terminology': r'\b(?:termin(?:e|i|ov|om|ih|u)?|terminolog(?:ia|ico|ica|ici|iche|ij\w*)|terminološk\w*|equivalent\w*|ustreznic\w*)\b',
    'morphology': r'\b(?:morfolog\w*|morpholog\w*|pregib\w*|lemma|lemmat\w*)\b|\bforma\s+base\b',
    'loris': r'\bloris\b',
    'synonyms': r'\b(?:sopomen\w*|sinonim\w*)\b',
    'collocations': r'\b(?:kolokacij\w*|collocaz\w*)\b',
}
_TERM_REQUEST = re.compile(r'\b(?:poišči|preveri\w*|najdi|navedi\w*|pokaži\w*|iščem|potrebujem|uporabi\w*|controlla\w*|verifica\w*|cerca\w*|trova\w*|mostra\w*|elenca\w*|consulta\w*|analizza\w*|individua\w*|dammi|vorrei)\b',re.I)
_TERM_TARGET_REQUEST = re.compile(r'\b(?:kater[iaoe]\s+(?:(?:so|je)\s+)?(?:termin(?:i|ov)?|ustreznic\w*)|qual[ei]\s+(?:(?:sono|è)\s+)?(?:(?:i|gli|le|il)\s+)?(?:termin[ei]|equivalent[ei]))\b',re.I)
_TERM_NOUN_REQUEST = re.compile(r'^\s*(?:termini|terminološk\w*\s+ustreznic\w*|equivalent\w*|ustreznic\w*)\s+(?:za|per|di)\b',re.I)
_QUOTES = re.compile(r'«([^«»\n]+)»|»([^»«\n]+)«|“([^“”\n]+)”|"([^"\n]+)"')


def plan(question, input_language='und'):
    wording=_QUOTES.sub(' ',question) # A tool name inside the quoted subject is not an operation.
    operations = [name for name,pattern in _PATTERNS.items() if re.search(pattern,wording,re.I)
                  and (name!='terminology' or _TERM_REQUEST.search(wording) or _TERM_NOUN_REQUEST.search(wording) or _TERM_TARGET_REQUEST.search(wording))]
    if not operations:return None
    spans = list(_QUOTES.finditer(question))
    # One explicit focus avoids silently selecting one of several unrelated terms.
    if len(spans)!=1:return {'version':VERSION,'operations':operations,'status':'clarification_required','reason':'one_quoted_focus_required'}
    match=spans[0];focus=next(x for x in match.groups() if x is not None)
    if not focus.strip()or len(focus)>512:return {'version':VERSION,'operations':operations,'status':'clarification_required','reason':'focus_outside_bound'}
    needs_language=any(x!='terminology'for x in operations)
    if needs_language and input_language not in {'sl','it'}:
        return {'version':VERSION,'operations':operations,'focus':focus,'status':'clarification_required','reason':'input_language_required'}
    return {'version':VERSION,'operations':operations,'focus':focus,'input_language':input_language,'status':'ready','routing_kind':'explicit_requested_operations_and_quoted_focus'}


def collect(gateway, family):
    routing=plan(family['question'],family.get('input_language','und'))
    if routing is None:return None
    result={'version':VERSION,'plan':routing,'captures':[],'capabilities_identity':None,'timings':{}}
    if routing['status']!='ready':return result
    focus=routing['focus'];operations=routing['operations'];started=time.monotonic()
    def capture(operation,request,response,ms):
        result['captures'].append({'operation':operation,'request':copy.deepcopy(request),'response':response,'response_sha256':digest(response),'duration_ms':ms})
        if len(canonical(result).encode())>MAX_BYTES:raise Failure('answer_unavailable',503)
    if 'terminology'in operations:
        if not gateway.klikai_url:raise Failure('task_unavailable',503)
        request={'q':focus,'allow_partial':'false','include_prose':'false','use_morphology':'true','limit':20}
        before=time.monotonic();response=gateway.request('/api/terminology/lookup?'+urlencode(request),None,gateway.klikai_url)
        frozen=terminology.freeze_lookup(response,focus,family['locale'])
        capture('terminology',request,frozen['bundle'],(time.monotonic()-before)*1000)
    lexical=[x for x in operations if x!='terminology']
    if lexical:
        if not gateway.linghub_url:raise Failure('task_unavailable',503)
        capabilities=gateway.request('/v1/capabilities',None,gateway.linghub_url)
        if not capabilities.get('identity')or not capabilities.get('analyzer_service_reachable'):raise Failure('task_unavailable',503)
        result['capabilities_identity']=capabilities['identity']
        request={'text':focus,'lang':routing['input_language'],'engine':'default','include':lexical,'limit':3}
        before=time.monotonic();response=gateway.request('/v1/explore',request,gateway.linghub_url)
        after=gateway.request('/v1/capabilities',None,gateway.linghub_url)
        if capabilities['identity']!=after.get('identity')or response.get('text')!=focus or response.get('lang')!=request['lang']or response.get('offset_convention')!='unicode_codepoint_half_open':raise Failure('resource_changed',503)
        capture('explore',request,response,(time.monotonic()-before)*1000)
    result['timings']['tool_evidence_ms']=(time.monotonic()-started)*1000
    result['capture_set_sha256']=digest(result['captures'])
    return result


def main():
    """Read-only CPU collation demonstration; never contacts a writer or pilot DB."""
    import argparse,json,os
    from pathlib import Path
    import httpx
    from .adapter import private_url
    parser=argparse.ArgumentParser()
    parser.add_argument('--question',required=True)
    parser.add_argument('--input-language',choices=['sl','it','und'],default='und')
    parser.add_argument('--locale',choices=['sl','it'],default='sl')
    parser.add_argument('--linghub-url',default='http://127.0.0.1:8010')
    parser.add_argument('--klikai-url',default='http://127.0.0.1:8000')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    class CPU:
        linghub_url=private_url(args.linghub_url);klikai_url=private_url(args.klikai_url)
        def request(self,path,payload,base):
            if base not in {self.linghub_url,self.klikai_url}or not(path in {'/v1/capabilities','/v1/explore'}or path.startswith('/api/terminology/lookup?')):raise ValueError('CPU-only operation required')
            with httpx.Client(base_url=base,timeout=10,trust_env=False,follow_redirects=False)as client:
                with client.stream('GET'if payload is None else'POST',path,**({}if payload is None else{'json':payload}))as response:
                    response.raise_for_status();raw=bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>8*1024*1024:raise ValueError('CPU response exceeds bound')
            return json.loads(raw)
    target=Path(args.output).absolute()
    if target.parent.resolve()!=target.parent or target.parent.stat().st_mode&0o077:raise ValueError('Use an existing private output directory')
    result=collect(CPU(),{'question':args.question,'input_language':args.input_language,'locale':args.locale})
    if result is None:raise ValueError('No explicit supported tool operation and quoted focus')
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w')as stream:stream.write(json.dumps({'tool_evidence':result},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['plan']['status'],'operations':result['plan']['operations'],'capture_sha256':digest(result),'output':str(target),'model_calls':0,'database_writes':0}))

if __name__=='__main__':main()
