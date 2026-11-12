"""Offline installed-population exercise; no endpoint, model or source mutation.

Consumes the separately frozen local manifest. Counts are source memberships,
not additive unique words or evidence of linguistic correctness. This exercises
the authoritative parser and its actual lexical-plan consumer. It does not run
analyzer APIs or claim a fully rendered answer for every entry.
"""
from __future__ import annotations
import argparse, collections, functools, hashlib, json, multiprocessing, os
from pathlib import Path
import sqlite3, sys, time, subprocess

def population_rows(pop):
    if 'population_sql' in pop:
        conn=sqlite3.connect('file:'+pop['path']+'?mode=ro',uri=True)
        try:yield from conn.execute(pop['population_sql'])
        finally:conn.close()
    else:
        with open(pop['path']) as source:
            for line in source:
                row=json.loads(line)
                if pop['kind']=='structured_term_or_alias':
                    if (row[0]==pop['source_filter'] and row[3]==pop['language']
                            and row[9] and row[10]):yield row[1],row[5]
                elif row[2]==pop['language']:yield row[0],row[1]

def heldout(population_id,stable_id):
    return int(hashlib.sha256((population_id+'\0'+str(stable_id)).encode()).hexdigest(),16)%10==0

def initialize(core,web):
    os.nice(10)
    sys.path[:0]=[core,web]
    # No application services are imported or invoked.
    global make_plan,consume_plan
    from tools.zrc_reference.request_interpretation import authoritative_request_plan
    from core.lexical_planner import plan
    make_plan=authoritative_request_plan;consume_plan=plan

@functools.lru_cache(maxsize=100000)
def exercise(term,language):
    pair=next(((a,b)for a,b in [('»','«'),('«','»'),('“','”'),('"','"')]if a not in term and b not in term),None)
    if pair is None:return 'no_unambiguous_quote_delimiter',None
    a,b=pair
    query=(f'Il termine {a}{term}{b} è appropriato?' if language=='it'
           else f'Je izraz {a}{term}{b} ustrezen?')
    try:
        request=make_plan(query,input_language=language,locale=language)
        targets=request.get('target_spans',[])
        if len(targets)!=1 or targets[0]['text']!=term:
            return 'authoritative_target_mismatch',request
        if request.get('requested_grammar_slots')or any(
                str(value).startswith('grammar_')for value in request.get('ambiguities',[])):
            return 'unintended_grammar_request',request
        target=targets[0]
        if query[target['start']:target['end']]!=term:
            return 'authoritative_span_mismatch',request
        consumer=consume_plan(query,'',language,{},request_plan=request)
        spans=consumer.get('targets',consumer.get('target_spans',[]))
        # lexical_planner uses target_spans in its portable profile.
        if len(spans)!=1 or spans[0]['text']!=term:
            return 'consumer_target_mismatch',{'request':request,'consumer':consumer}
        return 'passed',None
    except Exception as exc:return 'processing_exception',type(exc).__name__+': '+str(exc)

def exercise_population(pop):
    start=time.monotonic();counts=collections.Counter();held=collections.Counter();examples=[]
    cache_before=exercise.cache_info()
    language='sl'if pop['language']=='it-sl'else pop['language']
    for stable_id,term in population_rows(pop):
        if language not in {'sl','it'}:status,detail='unsupported_language',None
        elif not isinstance(term,str)or not term.strip():status,detail='empty_source_surface',None
        else:status,detail=exercise(term,language)
        counts[status]+=1
        if heldout(pop['id'],stable_id):held[status]+=1
        if status!='passed'and len(examples)<20:
            examples.append({'id':str(stable_id),'term':term,'status':status,'detail':detail})
    return {'population_id':pop['id'],'declared_population':pop['population_count'],
            'observed':sum(counts.values()),'counts':dict(counts),
            'identity_reserved_holdout_counts':dict(held),'failure_examples':examples,
            'parser_consumer_invocations':exercise.cache_info().misses-cache_before.misses,
            'identical_query_reuses':exercise.cache_info().hits-cache_before.hits,
            'elapsed_seconds':round(time.monotonic()-start,3)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--core',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args();path=Path(args.manifest);manifest=json.loads(path.read_text())
    web=str(Path(__file__).resolve().parents[1]);started=time.monotonic()
    output=Path(args.output)
    if output.exists():raise SystemExit('Preserve existing result: choose a new output path')
    report={'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'core_checkpoint':subprocess.check_output(['git','rev-parse','HEAD'],cwd=args.core,text=True).strip(),
        'web_checkpoint':subprocess.check_output(['git','rev-parse','HEAD'],cwd=web,text=True).strip(),
        'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':__doc__,'populations':[]}
    with multiprocessing.Pool(args.workers,initializer=initialize,initargs=(args.core,web))as pool:
        for result in pool.imap_unordered(exercise_population,manifest['populations']):
            report['populations'].append(result);print(json.dumps({k:result[k]for k in ['population_id','observed','counts','elapsed_seconds']},ensure_ascii=False),flush=True)
            output.with_suffix(output.suffix+'.partial').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    report['elapsed_seconds']=round(time.monotonic()-started,3)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
