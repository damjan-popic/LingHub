"""Frozen display of the existing lexical morphology API; no contextual inference."""
from copy import deepcopy
from .security import Failure
from .store import digest

CONTRACT='klikai.pilot.morphology-lexical.v1'

def freeze(data,tokens,lang,locale):
    if not isinstance(data,dict)or data.get('lang')!=lang or data.get('status')not in {'ok','no_hit'}or not isinstance(data.get('items'),list)or len(data['items'])!=len(tokens):raise Failure('invalid_answer',502)
    provenance=data.get('provenance')or{}
    if not provenance.get('fingerprint')or provenance.get('analysis_kind')!='lexical_candidates'or provenance.get('contextual')is not False:raise Failure('invalid_answer',502)
    for token,item in zip(tokens,data['items']):
        if item.get('input')!=token or not isinstance(item.get('candidates'),list)or len(item['candidates'])>4:raise Failure('invalid_answer',502)
        for candidate in item['candidates']:
            if not isinstance(candidate.get('lemma'),str)or candidate.get('language')!=lang or any(not isinstance(candidate.get(key),list)or any(not isinstance(v,str)for v in candidate[key])for key in ('msds','pos_families')):raise Failure('invalid_answer',502)
    body={'contract':CONTRACT,'task':'morphology','lang':lang,'locale':locale,'data':deepcopy(data)}
    return {'bundle':dict(body,snapshot_sha256=digest(body)),'identity':{'contract':CONTRACT,'captured_response_sha256':digest(body),'resource_fingerprint':provenance['fingerprint']}}

def checked(bundle):
    if bundle.get('contract')!=CONTRACT or bundle.get('snapshot_sha256')!=digest({k:v for k,v in bundle.items()if k!='snapshot_sha256'}):raise Failure('resource_changed',409)
    return bundle

def item_text(item,locale):
    it=locale=='it';lines=[item['input']]
    for candidate in item['candidates']:
        lines.append(('Lemma: 'if it else'Iztočnica: ')+candidate['lemma'])
        if candidate['msds']:lines.append(('Etichette grammaticali della fonte: 'if it else'Slovnične oznake vira: ')+', '.join(candidate['msds']))
        if candidate.get('matched_by')=='folded':lines.append('Corrispondenza senza distinzione di accenti.'if it else'Ujemanje brez razlikovanja diakritičnih znamenj.')
    if not item['candidates']:lines.append('Nessuna forma registrata trovata.'if it else'Zapisane oblike ni bilo mogoče najti.')
    if item.get('truncated'):lines.append('Sono mostrati i primi quattro candidati; l’elenco non è completo.'if it else'Prikazani so prvi štirje kandidati; seznam ni popoln.')
    return '\n'.join(lines)

def evidence(bundle):
    data=bundle['data'];p=data['provenance'];label=str(p['resource_id'])
    return [{'evidence_id':'morph:'+bundle['snapshot_sha256']+':'+str(i),'source_slug':label,'source_label':label,'title':item['input'],'source_revision':p['fingerprint']}for i,item in enumerate(data['items'])]

def answer(bundle,tier):
    checked(bundle)
    if tier!='short':raise Failure('expansion_unavailable',409)
    intro='Candidati lessicali registrati nella fonte. Non è una disambiguazione del contesto.'if bundle['locale']=='it'else'V viru zapisani leksikalni kandidati. To ni razdvoumljanje glede na sobesedilo.'
    blocks=[{'block_id':'summary','text':intro}]+[{'block_id':'morphology-'+str(i),'text':item_text(item,bundle['locale'])+f' [{i+1}]'}for i,item in enumerate(bundle['data']['items'])]
    label=str(bundle['data']['provenance']['resource_id'])
    return {'status':'validated'if bundle['data']['status']=='ok'else'no_hit','short_answer':intro,'answer_md':'\n\n'.join(b['text']for b in blocks),'display_blocks':blocks,'source_badges':[{'source_slug':label,'label':label}],'evidence':evidence(bundle),'answer_kind':'deterministic','answer_support':'supported','generation_status':'not_generated','deterministic_result':deepcopy(bundle['data']),'limitations':[]}

def source(bundle,eid,tier):
    checked(bundle)
    if tier!='short':raise Failure('not_found',404)
    for item,entry in zip(bundle['data']['items'],evidence(bundle)):
        if entry['evidence_id']==eid:
            p=bundle['data']['provenance']
            return {'evidence_id':eid,'source_label':str(p['resource_id']),'title':item['input'],'excerpt':item_text(item,bundle['locale']),'url':p.get('source_url'),'edition':p.get('version'),'bibliography':{'source_identifier':p.get('source_identifier'),'license':p.get('license'),'resource_fingerprint':p['fingerprint'],'analysis_kind':p['analysis_kind'],'contextual':False}}
    raise Failure('not_found',404)
