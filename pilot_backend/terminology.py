"""Frozen, exact terminology views over the accepted read-only lookup API."""
from copy import deepcopy
import unicodedata
import json
from .security import Failure
from .store import digest

CONTRACT='klikai.pilot.terminology-exact.v1'

def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC',value).lower().split())

def freeze_lookup(data,query,locale):
    if not isinstance(data,dict)or data.get('query')!=query or not isinstance(data.get('hits'),list)or len(data['hits'])>20:raise Failure('invalid_answer',502)
    hits=[]
    for hit in data['hits']:
        if not isinstance(hit,dict):raise Failure('invalid_answer',502)
        forms=[f for f in hit.get('matched_forms',[])if isinstance(f,dict)
               and f.get('match_tier')in {'exact','morphology_exact'}
               and f.get('field')in {'original','standardized','term','variantForms'}
               and type(f.get('ambiguity_cost'))is int and f['ambiguity_cost']==0
               and isinstance(f.get('matched_query'),str)and normalized(f['matched_query'])==normalized(query)
               and (f['match_tier']=='exact'or (f.get('lexicon_release_id')and f.get('query_signature')))]
        if not forms:continue
        for key in ('source_slug','source_display_name','external_id','citation_id','title','rights_mode','record_kind'):
            if not isinstance(hit.get(key),str)or not hit[key]:raise Failure('invalid_answer',502)
        if hit['source_slug']not in data.get('sources',[]):raise Failure('invalid_answer',502)
        for key in ('original_terms','standardized_terms','proposals','categories','subcategories','rationale'):
            if not isinstance(hit.get(key),list)or any(not isinstance(v,str)for v in hit[key]):raise Failure('invalid_answer',502)
        item=deepcopy(hit);item['accepted_matched_forms']=deepcopy(forms);hits.append(item)
    # Each record/sense remains separate. The digest identifies this captured
    # response, not an invented version of the live operational database.
    body={'contract':CONTRACT,'task':'terminology','query':query,'locale':locale,'sources':data.get('sources',[]),'hits':hits,'lookup_response_sha256':digest(data)}
    pin=digest(body)
    return {'bundle':{**body,'snapshot_sha256':pin},'identity':{'contract':CONTRACT,'captured_response_sha256':pin,'lexicon_release_ids':sorted({f['lexicon_release_id']for h in hits for f in h['accepted_matched_forms']if f.get('lexicon_release_id')})}}

def checked(bundle):
    if bundle.get('contract')!=CONTRACT or bundle.get('snapshot_sha256')!=digest({k:v for k,v in bundle.items()if k!='snapshot_sha256'}):raise Failure('resource_changed',409)
    return bundle

def record_text(hit,locale):
    it=locale=='it';lines=[hit['title']]
    labels=[('categories','Ambito'if it else'Področje'),('subcategories','Sottoambito'if it else'Podpodročje'),('original_terms','Forme originali'if it else'Izvirne oblike'),('standardized_terms','Forme standardizzate'if it else'Standardizirane oblike'),('proposals','Proposte nel record'if it else'Predlogi v zapisu'),('rationale','Motivazione originale'if it else'Izvirna utemeljitev')]
    for key,label in labels:
        if hit[key]:lines.append(label+':\n'+'\n'.join(hit[key]))
    if hit.get('term_entries'):
        lines.append(('Forme originali con etichette d’uso (nessuna etichetta non significa raccomandato)' if it else 'Izvirne oblike z oznakami rabe (manjkajoča oznaka ne pomeni priporočila)')+':\n'+json.dumps(hit['term_entries'],ensure_ascii=False,indent=2))
    lines.append(('Fonte: 'if it else'Vir: ')+hit['source_display_name'])
    if hit.get('source_date'):lines.append(('Data della fonte: 'if it else'Datum vira: ')+hit['source_date'])
    return '\n\n'.join(lines)

def evidence(bundle):
    return [{'evidence_id':'term:'+bundle['snapshot_sha256']+':'+str(index),'source_slug':hit['source_slug'],'source_label':hit['source_display_name'],'title':hit['title'],'external_id':hit['external_id'],'upstream_key':hit.get('upstream_key'),'rights_mode':hit['rights_mode'],'source_revision':bundle['snapshot_sha256'],'snippet':hit.get('snippet')or'','matched_forms':hit['accepted_matched_forms']}for index,hit in enumerate(bundle['hits'])]

def answer(bundle,tier):
    checked(bundle)
    if tier!='short':raise Failure('expansion_unavailable',409)
    it=bundle['locale']=='it';hits=bundle['hits']
    if not hits:
        notice='Nessuna corrispondenza terminologica esatta. Puoi riformulare il termine oppure scegliere Domanda per una ricerca diversa.'if it else'Ni natančnega terminološkega ujemanja. Izraz lahko preoblikujete ali izberete Vprašanje za drugačno iskanje.'
        return {'status':'no_hit','short_answer':notice,'answer_md':notice,'display_blocks':[{'block_id':'no-hit','text':notice}],'answer_kind':'deterministic','generation_status':'not_generated','source_badges':[],'evidence':[],'limitations':[]}
    intro='Corrispondenze esatte nei record terminologici. Ambiti e soluzioni di ciascun record sono mostrati separatamente.'if it else'Natančna ujemanja v terminoloških zapisih. Področja in rešitve posameznih zapisov so prikazani ločeno.'
    blocks=[{'block_id':'summary','text':intro}]+[{'block_id':'record-'+str(i),'text':record_text(h,bundle['locale'])+f' [{i+1}]'}for i,h in enumerate(hits)]
    badges=[];seen=set()
    for hit in hits:
        if hit['source_slug']not in seen:
            badges.append({'source_slug':hit['source_slug'],'label':hit.get('source_badge')or hit['source_display_name'],'display_name':hit['source_display_name'],'rights_mode':hit['rights_mode']});seen.add(hit['source_slug'])
    return {'status':'validated','short_answer':intro,'answer_md':'\n\n'.join(b['text']for b in blocks),'display_blocks':blocks,'answer_kind':'deterministic','answer_support':'supported','generation_status':'not_generated','source_badges':badges,'evidence':evidence(bundle),'deterministic_result':{'task':'terminology','query':bundle['query'],'snapshot_sha256':bundle['snapshot_sha256'],'records':deepcopy(hits)},'limitations':[]}

def source(bundle,eid,tier):
    checked(bundle)
    if tier!='short':raise Failure('not_found',404)
    for hit,item in zip(bundle['hits'],evidence(bundle)):
        if item['evidence_id']==eid:
            return {'evidence_id':eid,'source_label':hit['source_display_name'],'title':hit['title'],'excerpt':record_text(hit,bundle['locale']),'bibliography':{'source_date':hit.get('source_date'),'record_id':hit['external_id'],'rights_mode':hit['rights_mode']}}
    raise Failure('not_found',404)
