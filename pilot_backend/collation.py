"""Bounded task/focus routing over fixed, read-only CPU interfaces.

Tool names are operational facets, never ontology assignments. No question IDs,
model dispatcher or cross-resource sense joins occur here. Automatic rule lookup
uses explicit input language or language-specific question wording, not UI locale.
"""
from __future__ import annotations
import copy
import re
import unicodedata
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

# Applicability cues select a rule lookup, never a linguistic answer or ontology
# code. This is not a general text-language classifier or an instruction to
# correct all input.
_AUTO_INTENTS = {
    'sl': (
        ('capitalization', r'\b(?:začetnic\w*|(?:velik\w*|mal\w*)\s+črk\w*)\b'),
        ('place_name', r'\b(?:krajevn\w*|zemljepisn\w*|naselbinsk\w*)\s+imen\w*\b'),
        ('spelling', r'\b(?:kako|ali)\s+(?:(?:naj|se|pravilno)\s+)*(?:piš\w*|zapiš\w*|napiš\w*)\b'),
        ('lexical_choice', r'\b(?:ali|kaj|kater[ai])\s+(?:je|sta|so)\s+(?:prav|pravil\w*|ustrez\w*)\b'),
        ('lexical_meaning', r'\bkaj\s+pomen\w*\b'),
    ),
    'it': (
        ('capitalization', r'\b(?:maiuscol\w*|minuscol\w*)\b'),
        ('place_name', r'\bnom[ei]\s+(?:geografic\w*|di\s+luogo)\b'),
        ('spelling', r'\bcome\s+(?:si\s+)?scriv\w*\b'),
        ('lexical_choice', r'\b(?:è|sono)\s+(?:corrett\w*|giust\w*)\b'),
        ('lexical_meaning', r'\b(?:che\s+cosa|cosa|che)\s+significa\w*\b'),
    ),
}
_TARGET_LANGUAGE = {
    'sl': re.compile(r'\b(?:v\s+slovenščini|in\s+sloveno)\b', re.I),
    'it': re.compile(r'\b(?:v\s+italijanščini|in\s+italiano)\b', re.I),
}


def single_word_plan(question, input_language='und'):
    """Original Unicode span; SL/IT is a lookup policy, not language detection."""
    if not isinstance(question,str) or len(question)>512:return None
    start,end=0,len(question)
    while start<end and question[start].isspace():start+=1
    while end>start and (question[end-1].isspace() or question[end-1] in '?!.,;:'):end-=1
    pairs={'"':'"', "'":"'", '«':'»','»':'«','“':'”','‘':'’'}
    if start<end and pairs.get(question[start])==question[end-1]:start+=1;end-=1
    while start<end and question[start].isspace():start+=1
    while end>start and (question[end-1].isspace() or question[end-1] in '?!.,;:'):end-=1
    word=question[start:end]
    # Same Unicode-letter token convention as documentary query handling, with
    # combining marks kept attached; no URLs, digits, punctuation-only input.
    if not word or not word[0].isalpha() or any(not(c.isalpha() or unicodedata.category(c).startswith('M')) for c in word):return None
    languages=[input_language] if input_language in {'sl','it'} else ['sl','it']
    return {'version':VERSION,'operations':['loris'],'focus':word,'input_language':input_language,
        'lookup_languages':languages,'status':'ready','routing_kind':'single_word_rule_lookup',
        'reason':'single_word_rule_lookup','language_basis':'explicit_input_language' if len(languages)==1 else 'project_sl_it_inventory_lookup_not_detection',
        'focus_span':{'start':start,'end':end,'offset_basis':'original_question_unicode_codepoint_half_open'}}


def automatic_plan(question, input_language='und'):
    """Select a bounded LORIS check without interpreting its normative result.

    Preserve comparison alternatives verbatim. With one unambiguous quoted
    subject, retain its exact original-question span as well as analyzer-local
    offsets. Ambiguous/mixed-language or long questions continue source lookup
    without guessing a tool input or forcing a clarification.
    """
    if not isinstance(question,str) or not question.strip() or len(question)>512:
        return None
    word=single_word_plan(question,input_language)
    if word:return word
    wording=_QUOTES.sub(' ',question)
    cues=[(language,intent) for language,patterns in _AUTO_INTENTS.items()
          for intent,pattern in patterns if re.search(pattern,wording,re.I)]
    if not cues or input_language=='mixed':return None
    targets=[language for language,pattern in _TARGET_LANGUAGE.items() if pattern.search(wording)]
    if len(targets)>1:return None
    if input_language in {'sl','it'}:
        language=input_language;basis='explicit_input_language'
    elif targets:
        language=targets[0];basis='explicit_target_language_wording'
    else:
        languages={language for language,_ in cues}
        if len(languages)!=1:return None
        language=next(iter(languages));basis='language_specific_question_wording'
    spans=list(_QUOTES.finditer(question))
    start,end=0,len(question)
    # A comparison may quote only one alternative. Keep all alternatives and
    # their casing; never synthesize a normalized replacement phrase.
    comparison=re.search(r'\b(?:ali|o|oppure)\b',wording,re.I)
    if len(spans)==1 and not comparison:
        match=spans[0]
        group=next(i for i,value in enumerate(match.groups(),1) if value is not None)
        start,end=match.span(group)
    focus=question[start:end]
    if not focus.strip():return None
    return {'version':VERSION,'operations':['loris'],'focus':focus,
            'input_language':language,'status':'ready',
            'routing_kind':'automatic_question_rule_lookup',
            'reason':cues[0][1],'language_basis':basis,
            'focus_span':{'start':start,'end':end,
                          'offset_basis':'original_question_unicode_codepoint_half_open'}}


def plan(question, input_language='und', focus=None):
    if focus is None and single_word_plan(question,input_language):return None
    wording=_QUOTES.sub(' ',question) # A tool name inside the quoted subject is not an operation.
    operations = [name for name,pattern in _PATTERNS.items() if re.search(pattern,wording,re.I)
                  and (name!='terminology' or _TERM_REQUEST.search(wording) or _TERM_NOUN_REQUEST.search(wording) or _TERM_TARGET_REQUEST.search(wording))]
    if not operations:return None
    explicit_focus=focus is not None
    spans = list(_QUOTES.finditer(question))
    # One explicit focus avoids silently selecting one of several unrelated terms.
    if not explicit_focus:
        if len(spans)!=1:return {'version':VERSION,'operations':operations,'status':'clarification_required','reason':'one_quoted_focus_required'}
        match=spans[0];focus=next(x for x in match.groups() if x is not None)
    if not focus.strip()or len(focus)>512:return {'version':VERSION,'operations':operations,'status':'clarification_required','reason':'focus_outside_bound'}
    needs_language=any(x!='terminology'for x in operations)
    if needs_language and input_language not in {'sl','it'}:
        return {'version':VERSION,'operations':operations,'focus':focus,'status':'clarification_required','reason':'input_language_required'}
    return {'version':VERSION,'operations':operations,'focus':focus,'input_language':input_language,'status':'ready','routing_kind':'explicit_requested_operations_and_focus_field'if explicit_focus else'explicit_requested_operations_and_quoted_focus'}


def collect(gateway, family, *, automatic=False):
    question=family.get('original_question',family['question'])
    routing=single_word_plan(question,family.get('input_language','und')) if automatic and not family.get('focus') else None
    if routing is None:routing=plan(question,family.get('input_language','und'),family.get('focus'))
    if routing is None and automatic:
        routing=automatic_plan(question,family.get('input_language','und'))
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
        for language in routing.get('lookup_languages',[routing['input_language']]):
            request={'text':focus,'lang':language,'engine':'default','include':lexical,'limit':3}
            before=time.monotonic()
            try:
                response=gateway.request('/v1/explore',request,gateway.linghub_url)
            except Failure as error:
                if routing.get('routing_kind')!='single_word_rule_lookup':raise
                # Explicitly local transport observation, never an analyzer result.
                response={'text':focus,'lang':language,'offset_convention':'unicode_codepoint_half_open',
                    'results':{op:{'status':'unavailable','data':None,'diagnostic':{'stage':'explore','error_code':error.code,'error_type':type(error).__name__}} for op in lexical},
                    'observation_origin':'application_transport_failure'}
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
