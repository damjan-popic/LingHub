"""Reuse ordinary source-only and lexical readers for located document text."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from copy import deepcopy
import re,time
from .store import digest,canonical
from . import unified,collation
from .document_resource_context import VERSION

MAX_OCCURRENCES=24
MAX_REFERENCE_SECONDS=45
MAX_COLLECTION_SECONDS=180
WORDS=re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*",re.UNICODE)
FUNCTIONS={'tudi','bomo','bodo','smo','sta','kjer','zato','kako','torej','lahko','vseh','vsem','njegovo','njihov','istega','njen','njih','svoj'}

def occurrences(task):
    """Discover the entire original section before applying any lookup bound."""
    grouped={};omitted=[]
    for match in WORDS.finditer(task['source_text']):
        value=match.group();span={'start':match.start(),'end':match.end(),'text':value}
        if len(value)<3 or any(c.isdigit()for c in value)or value.casefold()in FUNCTIONS:
            omitted.append({**span,'reason':'not_a_supported_independent_lexical_target'});continue
        grouped.setdefault(value,[]).append(span)
    items=[]
    for word,spans in grouped.items():
        item={'text':word,'spans':spans}
        if len(items)>=MAX_OCCURRENCES:omitted.extend({**span,'reason':'document_lexical_lookup_work_bound'}for span in spans)
        else:items.append(item)
    return items,omitted

def collect(gateway,task,discovery):
    started=time.monotonic();envelopes=[];coverage=[];text=task['source_text'];binding=task.get('document_binding')or{}
    section=binding.get('section_index',0)
    def occurrence(a,b):return {'section':section,'start':a,'end':b,'text':text[a:b],'original_sha256':task['source_sha256']}
    for window in discovery['windows']:
        if time.monotonic()-started>=MAX_REFERENCE_SECONDS:
            coverage.extend({'family':family,'queried':False,'status':'bounded_partial','reason':'document_reference_time_bound',
                'input_span':{k:window[k]for k in ('start','end')}}for family in ('jezikovna_svetovalnica','epravopis'))
            continue
        request={'question':window['query'],'sources':['jezikovna_svetovalnica','epravopis'],
            'retrieval':'lexical','answer_language':task['locale'],'limit':8}
        envelope={'occurrence':occurrence(window['start'],window['end']),'query':window['query'],
            'language':task['source_language'],'source_passages':[],'tool_passages':[],'coverage':[],'source_snapshot_ids':[]}
        try:
            response=gateway.request('/internal/pilot/source-context',request)
            if (response.get('contract')!='klikai.reference-source-context.v1'or response.get('source_only')is not True
                    or response.get('generation',{}).get('attempted')is not False):raise ValueError('Source-only reference contract differs')
            envelope.update(source_passages=deepcopy(response['passages']),coverage=deepcopy(response['coverage']),
                source_snapshot_ids=[response['snapshot_id']],reference_request=request,reference_response=deepcopy(response))
        except Exception as error:
            envelope['coverage']=[{'family':family,'queried':True,'status':'unavailable',
                'reason':'ordinary_reference_context_unavailable','returned_count':0,**unified.diagnostic(error,'document_ordinary_reference')}for family in request['sources']]
        envelopes.append(envelope);coverage.extend(envelope['coverage'])
    inputs,omitted=occurrences(task)
    def lexical(item):
        word=item['text'];span=item['spans'][0]
        a=max(0,span['start']-1400);b=min(len(text),a+3000);a=max(0,b-3000)
        context=text[a:b]
        family={'question':word,'original_question':word,'context':context,
            'input_language':task['source_language'],'locale':task['locale']}
        envelope={'occurrence':occurrence(span['start'],span['end']),'all_occurrences':[occurrence(s['start'],s['end'])for s in item['spans']],
            'query':word,'language':task['source_language'],'source_passages':[],'tool_passages':[],'coverage':[],
            'source_snapshot_ids':[],'context_span':{'start':a,'end':b,'complete_section':a==0 and b==len(text)}}
        try:
            interpretation=unified.shared_interpretation(gateway,word,context,task['source_language'],task['locale'])
            family['request_plan']=interpretation['request_plan']
            capture,observed=unified.collect_tools(gateway,family)
            envelope['lookup_status']=deepcopy(observed)
            if capture is not None and capture.get('version')=='klikai.lexical-collation.v1':
                envelope['tool_evidence']=capture
                p=capture['captures'][0]['response']
                envelope['coverage']=[{'family':name,'queried':value.get('status')not in {'not_requested','not_integrated'},
                    'status':value.get('status','unavailable'),'reason':value.get('reason'),
                    'target':word,'occurrences':deepcopy(item['spans'])}for name,value in p.get('facets',{}).items()]
            else:envelope['coverage']=[{'family':name,'queried':False,'status':'unavailable',
                'reason':observed.get('reason')or'ordinary_lexical_profile_unavailable','target':word}for name in ('morphology','synonyms','collocations','loris')]
        except Exception as error:
            envelope['coverage']=[{'family':name,'queried':True,'status':'unavailable','target':word,
                **unified.diagnostic(error,'document_ordinary_lexical')}for name in ('morphology','synonyms','collocations','loris')]
        return envelope
    with ThreadPoolExecutor(max_workers=4)as pool:
        futures=[pool.submit(lexical,item)for item in inputs]
        lexical_envelopes=[future.result()for future in as_completed(futures)]
    lexical_envelopes.sort(key=lambda e:e['occurrence']['start'])
    # Reuse CORE's pure normal projection once per bounded complete capture.
    # A failed resource/projection remains a precise limitation, not loss of
    # the available documentary or other lexical results.
    for envelope in lexical_envelopes:
        capture=envelope.get('tool_evidence')
        if capture is not None and time.monotonic()-started>=MAX_COLLECTION_SECONDS:
            envelope['coverage'].append({'family':'lexical_projection','queried':False,'status':'bounded_partial','reason':'document_context_time_bound'})
            coverage.extend(envelope['coverage']);envelopes.append(envelope);continue
        if capture is not None:
            try:
                response=gateway.request('/internal/pilot/document-resource-context/project',{'captures':[capture]})
                if response.get('version')!=VERSION or response.get('generation',{}).get('attempted')is not False:raise ValueError('Ordinary projection contract differs')
                projection,=response['projections']
                if projection['capture_set_sha256']!=capture['capture_set_sha256']:raise ValueError('Ordinary projection identity differs')
                envelope.update(tool_passages=projection['passages'],projection_diagnostics=projection['diagnostics'])
            except Exception as error:
                envelope['coverage'].append({'family':'lexical_projection','queried':False,'status':'unavailable',
                    **unified.diagnostic(error,'document_ordinary_projection')})
        coverage.extend(envelope['coverage']);envelopes.append(envelope)
    value={'version':VERSION,'source_sha256':task['source_sha256'],'envelopes':envelopes,
        'coverage':coverage,'lexical_discovery':{'whole_section_discovered':True,'requested':inputs,'omitted':omitted,'lookup_limit':MAX_OCCURRENCES},
        'elapsed_ms':round((time.monotonic()-started)*1000,3),'generation':{'attempted':False,'inference_performed':False,'scope':'general_writer_only'},'dedicated_analysis_accounting':'Exact native captures/statuses retained; no aggregate zero-inference claim for analyzers'}
    value['context_sha256']=digest(value)
    return value
