"""Bounded complete paradigms for source-nominated case constructions."""
from .store import digest
from . import unified
from pathlib import Path
import hashlib,json
def application_registry():
    text=Path(__file__).with_name('document_source_applications.v1.json').read_text()
    return json.loads(text),hashlib.sha256(text.encode()).hexdigest()
def bound_entries(raw):
    text=raw.get('original_text','')
    for index,entry in enumerate(application_registry()[0]['entries']):
        if any(raw.get(k)!=v for k,v in entry['source_metadata'].items()):continue
        a,b=entry['row_span']
        if hashlib.sha256(text.encode()).hexdigest()==entry['body_sha256']and hashlib.sha256(text[a:b].encode()).hexdigest()==entry['row_sha256']:
            yield index,entry
def lexical_candidates(captures,word):
    result=[]
    for row in captures.get('retrieval',[]):
        if row.get('family')!='document_lexical_context':continue
        for capture in row.get('captures',[]):
            for item in capture.get('bundle',{}).get('data',{}).get('items',[]):
                if item.get('input')==word and not item.get('truncated'):
                    result.extend(c for c in item.get('candidates',[])if c.get('matched_by')=='exact')
    return result
def freeze_morphology(gateway,task,bundle):
    from .document_surface import occurrences
    import re
    evidence=bundle['unified_evidence'];lemmas=set()
    for record in evidence['matches']:
        for _,entry in bound_entries(record['capture']):
            if entry['kind']!='case_government':continue
            for found in occurrences(entry['prefix'],task['source_text']):
                tail=re.match(r'\s+([^\W\d_]+)\b',task['source_text'][found['end']:])
                if tail:
                    for candidate in lexical_candidates(evidence,tail[1]):
                        if 'S'in candidate.get('pos_families',[]):lemmas.add(candidate['lemma'])
    out=[]
    for lemma in sorted(lemmas)[:8]:
        payload={'lang':'sl','lemma':lemma,'limit':100}
        try:
            response=gateway.request('/v1/morphology/inflect',payload,gateway.linghub_url)
            out.append({'request':payload,'response':response,'response_sha256':digest(response)})
        except Exception as error:out.append({'request':payload,'status':'unavailable',**unified.diagnostic(error,'application_morphology')})
    return out
