"""Original submitted-use scopes; no discovery, analysis or linguistic decisions."""
import re

RESOURCES={'synonyms':'cjvt-thesaurus','collocations':'cjvt-ksss'}


def lexical_context_scope(plan,language):
    if not isinstance(plan,dict)or language!='sl':return None
    facets=set(plan.get('requested_facets',[]));consult=plan.get('consultation')or{}
    if (plan.get('ambiguities')or plan.get('requested_grammar_slots')or plan.get('comma_check')
            or plan.get('requested_edition')is not None or plan.get('translation_language')
            or plan.get('requested_source')not in {None,*RESOURCES.values()}
            or facets&{'orthography','spacing','frequency','morphology','inflection','conjugation','translation'}):return None
    explicit=facets&RESOURCES.keys()
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
