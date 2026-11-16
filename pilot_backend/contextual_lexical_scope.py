"""Original submitted-use scopes; no discovery, analysis or linguistic decisions."""
import re
import hashlib,json
from copy import deepcopy

RESOURCES={'synonyms':'cjvt-thesaurus','collocations':'cjvt-ksss'}
SUPPORT_VERSION='original-occurrence-support-v1'
# Existing profile limit=4, with the two existing native relation resources.
# This is one distinct-work window, reused rather than renewed by enrichment.
SUPPORT_LIMIT=8
RESOURCE_CLASSES={kind:frozenset({'NOUN','ADJ','VERB','ADV'})for kind in RESOURCES}

def _identity(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def supporting_lookups(plan,language,analysis,limit=4):
    """Nominate existing original observations, without creating user targets.

    No analysis or lookup is performed here. Ambiguous observations stay
    ambiguous; names/function words stay in the unmodified original analysis.
    """
    task=lexical_context_scope(plan,language)
    consult=(plan or{}).get('consultation')or{}
    if (task is None or plan.get('target_spans')or consult.get('requested')is not False
            or plan.get('source_language')not in {None,'und',language}
            or consult.get('kind')not in {'example','expression','judgment'}):return None
    scope=task['target_scope'];text=plan['original']+('\n'+plan.get('context','')if plan.get('context')else'')
    budget=min(SUPPORT_LIMIT,max(1,min(4,limit))*len(task['kinds']))
    report={'version':SUPPORT_VERSION,'target_scope':deepcopy(scope),'work_limit':budget,
        'status':'analysis_unavailable','targets':[],'observed_occurrences':[],
        'context_observations':[],'omissions':[],'work_ids':[]}
    if not isinstance(analysis,dict)or analysis.get('text')!=text:return report
    raw=analysis.get('raw_engines')or{}
    if not raw:return report
    original={engine:{'model':value.get('analysis',{}).get('model'),
        'words':value.get('analysis',{}).get('words',[])}for engine,value in raw.items()}
    analysis_id=_identity({'text':text,'observations':original})
    report.update(analysis_identity=analysis_id,status='ready')
    shift=len(plan['original'])+1 if scope['scope']=='context'else 0
    observations={}
    for engine,value in original.items():
        words=value['words'];seen=set()
        for word in words:
            a,b=word.get('start'),word.get('end')
            if (type(a)is not int or type(b)is not int or not 0<=a<b<=len(text)
                    or text[a:b]!=word.get('text')or type(word.get('id'))is not int):
                return dict(report,status='analysis_binding_invalid',targets=[],work_ids=[])
            if not shift+scope['start']<=a<b<=shift+scope['end']:continue
            key=(a,b,word['text'])
            if key in seen:return dict(report,status='analysis_binding_invalid',targets=[],work_ids=[])
            seen.add(key)
            observations.setdefault(key,[]).append({'engine':engine,'word_id':word['id'],
                'lemma':word.get('lemma'),'upos':word.get('upos'),'feats':deepcopy(word.get('feats',{}))})
    groups={};context_groups={}
    for (a,b,surface),predictions in sorted(observations.items()):
        readings={(p['lemma'],p['upos'])for p in predictions if isinstance(p['lemma'],str)}
        occurrence={'scope':scope['scope'],'start':a-shift,'end':b-shift,'text':surface,
            'analysis_start':a,'analysis_end':b,'scope_start':a-shift-scope['start'],
            'scope_end':b-shift-scope['start'],'predictions':predictions}
        if any(c<b and a<d and(c,d)!=(a,b)for c,d,_ in observations):
            report['omissions'].append({'occurrence':occurrence,'reason':'ambiguous_original_segmentation'});continue
        if len(readings)!=1 or len(readings)<len({(p['lemma'],p['upos'])for p in predictions}):
            report['omissions'].append({'occurrence':occurrence,'reason':'ambiguous_original_reading'});continue
        lemma,upos=next(iter(readings))
        if not lemma.strip()or not isinstance(upos,str)or not upos.strip():
            report['omissions'].append({'occurrence':occurrence,'reason':'ambiguous_original_reading'});continue
        # Contextual presence includes all unambiguous observed classes.
        # It neither nominates reader work nor proves grammatical attachment.
        context_group=context_groups.setdefault((lemma.casefold(),upos),
            {'lemma':lemma,'upos':upos,'occurrences':[]})
        context_group['occurrences'].append(occurrence)
        kinds=[kind for kind in task['kinds']if upos in RESOURCE_CLASSES[kind]]
        if not kinds or not re.fullmatch(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*",lemma):
            report['omissions'].append({'occurrence':occurrence,'reason':'no_supported_resource_class'});continue
        key=(lemma.casefold(),upos)
        group=groups.setdefault(key,{'lemma':lemma,'upos':upos,'occurrences':[],'kinds':kinds})
        group['occurrences'].append(occurrence)
    # An observation remains context even when its lookup is outside the work
    # window. It can locate a partner in a source fact without a new lookup.
    report['observed_occurrences']=deepcopy(list(groups.values()))
    report['context_observations']=deepcopy(list(context_groups.values()))
    for group in groups.values():
        work=[(kind,_identity([SUPPORT_VERSION,analysis_id,kind,group['lemma'].casefold()]))for kind in group['kinds']]
        retained=[];omitted=[]
        for kind,ident in work:
            if ident in report['work_ids']or len(report['work_ids'])<budget:
                retained.append((kind,ident))
                if ident not in report['work_ids']:report['work_ids'].append(ident)
            else:omitted.append(kind)
        if omitted:report['omissions'].append({'lemma':group['lemma'],'upos':group['upos'],'reason':'shared_distinct_work_bound','omitted_kinds':omitted})
        if not retained:continue
        origin={'version':SUPPORT_VERSION,'analysis_identity':analysis_id,'target_scope':deepcopy(scope),
            'lemma':group['lemma'],'upos':group['upos'],'occurrences':group['occurrences'],
            'kinds':[k for k,_ in retained],'work_ids':[ident for _,ident in retained]}
        first=group['occurrences'][0]
        report['targets'].append({'text':first['text'],'start':first['analysis_start'],'end':first['analysis_end'],
            'lemma_candidates':[group['lemma']],'lemma_sequences':[group['lemma']],
            'context_features':[{'engine':p['engine'],'upos':p['upos'],'feats':deepcopy(p['feats'])}for p in first['predictions']],
            'supporting_origin':origin})
    if not report['targets']:report['status']='no_supported_work'
    return report


def _source_only_wrapper(question):
    """Veto optional analysis for a single explicit source-navigation request.

    This cannot add an intent, target, route or source. A mixed request keeps
    its authoritative task handling rather than being reduced to its prefix.
    """
    clauses=[s.strip()for s in re.split(r'[.!?;]\s*',question)if s.strip()]
    return len(clauses)==1 and bool(re.match(
        r'\s*(?:kje\s+(?:lahko\s+)?(?:najdem|poiščem)|kateri\s+(?:vir|viri|slovar)|'
        r'iz\s+katerega\s+(?:vira|slovarja)|(?:navedi|pokaži|prikaži)\s+(?:mi\s+)?(?:samo|le)\s+(?:vire|vir|slovar\w*)|'
        r'dove\s+(?:trovo|posso\s+trovare)|da\s+quale\s+(?:fonte|dizionario)|'
        r'(?:mostra|indica|elenca)\s+(?:solo|soltanto)\s+(?:le\s+)?fonti|'
        r'which\s+(?:source|dictionary)|(?:show|list)\s+only\s+(?:the\s+)?sources)\b',clauses[0],re.I))


def lexical_context_scope(plan,language):
    if not isinstance(plan,dict)or language!='sl':return None
    facets=set(plan.get('requested_facets',[]));consult=plan.get('consultation')or{}
    if (plan.get('ambiguities')or plan.get('requested_grammar_slots')or plan.get('comma_check')
            or plan.get('requested_edition')is not None or plan.get('translation_language')
            or plan.get('requested_source')not in {None,*RESOURCES.values()}
            or facets&{'orthography','spacing','frequency','morphology','inflection','conjugation','translation'}):return None
    explicit=facets&RESOURCES.keys()
    if _source_only_wrapper(plan.get('original','')):return None
    if not explicit and not (consult.get('requested')is False
            and {'usage','register'}&set(consult.get('supporting_dimensions',[]))):return None
    question=plan.get('original','');context=plan.get('context','');scope=None
    def bound(host,a,b,origin):
        if (isinstance(host,str)and type(a)is int and type(b)is int and 0<=a<b<=len(host)
                and len(re.findall(r"[^\W_]+(?:[-’'][^\W_]+)*",host[a:b]))>=2):
            return {'scope':origin,'start':a,'end':b,'text':host[a:b]}
    if context.strip():scope=bound(context,len(context)-len(context.lstrip()),len(context.rstrip()),'context')
    if scope is None:
        supplied=consult.get('scope')or{}
        if consult.get('kind')in {'example','expression'}:
            scope=bound(question,supplied.get('start'),supplied.get('end'),'question')
        else:
            # Explicit relations may have a quoted submitted phrase. A relation
            # object's wrapper/context_span is never itself an analysis target.
            targets=plan.get('target_spans')or[]
            if len(targets)==1:
                target=targets[0]
                if target.get('basis')=='quotation' or (consult.get('kind')=='judgment'
                        and target.get('text')!=supplied.get('text')):
                    if target.get('span_scope')=='context':
                        shift=len(question)+1
                        scope=bound(context,target.get('start',0)-shift,target.get('end',0)-shift,'context')
                    else:scope=bound(question,target.get('start'),target.get('end'),'question')
    if scope is None:return None
    excluded=set((plan.get('lexical_request')or{}).get('excluded_tasks',[]))
    kinds=explicit or RESOURCES.keys()-excluded
    source=plan.get('requested_source')
    kinds=sorted(k for k in kinds if source in {None,RESOURCES[k]})
    return {'target_scope':scope,'kinds':kinds}if kinds else None
