"""Recorded, deterministic guidance for a small verified resource registry.

No generated URLs, live crawling, account-history inference or linguistic
equivalence claims. The registry is captured with each answer for inspection.
"""
import re
from copy import deepcopy
from .security import Failure
from .store import digest
from . import documents

CONTRACT = 'klikai.request-guidance.v1'
VERIFIED = '2026-10-18'
RESOURCES = {
    'zvezek': ('Zvezek', 'https://zvezek.jeziknaklik.it/'),
    'forms': ('Obrazci in gradivo', 'https://www.jeziknaklik.it/obrazci-gradivo/'),
    'bank': ('ZKB – bančni glosar', 'https://www.jeziknaklik.it/zkb/glosar/'),
    'school': ('Informascuole – dvojezični šolski vodnik', 'https://www.jeziknaklik.it/solstvo/solski-vodnik-dvojezicni-vodnik-za-izbiro-po-nizji-srednji/'),
    'constitution': ('Slovenski prevod italijanske ustave', 'https://www.jeziknaklik.it/publikacije/slovenski-prevod-italijanske-ustave-zdaj-na-voljo-na-spletni-strani-senata/'),
}

def unresolved_reference(question, context=''):
    from core.lexical_planner import is_referential_request, bound_context_target
    return is_referential_request(question) and bound_context_target(question,context) is None

def interpret(question, context='', locale='sl'):
    q = question.casefold().strip(); it = locale == 'it'
    if unresolved_reference(question, context):
        return {'kind':'missing_referent', 'text': 'A quale espressione ti riferisci? Scrivila insieme alla domanda.' if it else 'Na kateri izraz se nanaša »to«? Prosim, napišite ga ob vprašanju.', 'resources':[]}
    navigation = bool(re.search(r'\b(kje|kam|najdem|dobim|iščem|poišči|prenesem|prenesti|potrebujem|rabim|pokaži|scaricare|dove|trovo|trovare|cerco|vorrei|download)\b', q))
    service = bool(re.search(r'\b(ne dela|ne deluje|non funziona)\b', q))
    constitution = bool(re.search(r'\b(?:ustav[aoe]|costituzion[ei])\b', q))
    # A resource name inside a language question is still a linguistic target.
    # A translated constitution is a document lookup, unlike a word translation.
    language_question = bool(re.search(r'\b(?:sopomen\w*|sinonim\w*|sklon\w*|sklanj\w*|pomen\w*|significa\w*|besed\w*|parol\w*|izraz\w*|espression\w*|stavk\w*|frase|uporab\w*)\b', q))
    language_question |= not constitution and bool(re.search(r'\b(?:prevod\w*|preved\w*|traduc\w*|traduzion\w*)\b', q))
    if language_question:return None
    # Filing a document is not navigation to a blank form or a dictionary.
    if re.search(r'\b(?:kam|kje|kjer[ia]|dove|ku)\b.*\b(?:oddat(?:i)?|oddam\w*|presentare|presento|consegnare|consegno|sportello)\b', q) or re.search(r'\bsportello\b.*\b(?:kam|kje|dove)\b',q):
        return {'kind':'service_location_unverified', 'text': 'Non ho informazioni verificate sullo sportello competente per questa pratica. Per indicarlo servono il tipo di pratica e il Comune.' if it else 'Za ta postopek nimam preverjenega podatka o pristojnem okencu. Za usmeritev sta potrebna vrsta postopka in občina.', 'resources':[]}
    key = None
    physical_notebook = re.search(r'\b(?:(?:moj\w*|tvoj\w*)\s+zvez\w*|kup\w*|prodaj\w*|papir\w*|torb\w*|polic\w*)\b', q)
    if re.search(r'\bzvez(?:ek|ka|ku|kom)\b',q) and (navigation or service) and not physical_notebook: key = 'zvezek'
    elif navigation and re.search(r'\b' + documents.GENRES + r'\b', q):
        return {'kind': 'document_search', 'text': '', 'resources': []}
    elif navigation and (('bančn' in q and ('slovar' in q or 'glosar' in q)) or 'glossario bancario' in q): key = 'bank'
    elif navigation and 'informascuole' in q: key = 'school'
    elif navigation and constitution:
        italian_constitution = re.search(r'\b(?:italijansk\w*\s+ustav[aoie]|ustav[aoie]\s+italije|costituzion[ei]\s+italian[ae]|italian[ae]\s+costituzion[ei])\b',q)
        if not italian_constitution:
            return {'kind':'resource_scope_required', 'text': 'Di quale Paese cerchi la costituzione?' if it else 'Ustavo katere države iščete?', 'resources':[]}
        key = 'constitution'
    if key:
        title, url = RESOURCES[key]
        text = ('La risorsa è disponibile qui: ' if it else 'Vir najdete tukaj: ') + title + '. [1]'
        kind = 'source_guidance'
        if key == 'zvezek' and re.search(r'prenes|prenesti|računalnik|scaric|download', q):
            text = ('Zvezek è disponibile come applicazione web. Non ho un collegamento verificato per scaricarlo e usarlo offline. [1]' if it else 'Zvezek je na voljo kot spletna aplikacija. Preverjene povezave za prenos in uporabo brez spleta nimam. [1]')
        elif key == 'zvezek' and service:
            kind = 'service_report'
            text = ('Il collegamento registrato a Zvezek è questo. Non posso confermare la causa del problema. Nell’opinione sulla risposta puoi indicare il messaggio di errore e cosa stavi facendo. [1]' if it else 'To je zabeležena povezava do Zvezka. Vzroka težave ne morem potrditi. V povratni informaciji lahko navedete sporočilo napake in kaj ste poskušali narediti. [1]')
        return {'kind':kind, 'text':text, 'resources':[{'key':key,'title':title,'url':url,'verified_at':VERIFIED,'capability':'web_access'}]}
    if navigation and re.search(r'\b(obrazec|obrazci|slovar|slovarček|glosar|vodnik|modulo|dizionario)\b', q):
        return {'kind':'resource_not_registered', 'text': 'Non ho un collegamento verificato a questa risorsa. Puoi indicarne il titolo o l’ente responsabile?' if it else 'Za ta vir nimam preverjene povezave. Lahko navedete naslov ali ustanovo, ki ga objavlja?', 'resources':[]}
    return None

def freeze(family, gateway=None):
    decision = interpret(family['question'], family.get('context',''), family.get('locale','sl'))
    if decision is None: return None
    if decision['kind'] == 'document_search':
        decision = documents.lookup(gateway, family['question'], family.get('locale', 'sl'))
    body = {'contract':CONTRACT, 'locale':family.get('locale','sl'), 'question':family['question'], 'context':family.get('context',''), 'decision':decision}
    return {'bundle':{**body,'snapshot_sha256':digest(body)}, 'identity':{'contract':CONTRACT,'captured_response_sha256':digest(body)}}

def checked(bundle):
    if bundle.get('contract') != CONTRACT or bundle.get('snapshot_sha256') != digest({k:v for k,v in bundle.items() if k!='snapshot_sha256'}): raise Failure('resource_changed',409)

def evidence(bundle):
    checked(bundle)
    return [{'evidence_id':'guidance:'+bundle['snapshot_sha256']+':'+str(i),'local_number':i+1,'source_slug':r.get('source_slug','jezik_na_klik'),'source_label':r.get('source_label','Jezik na klik'),'title':r['title'],'url':r['url']} for i,r in enumerate(bundle['decision']['resources'])]

def answer(bundle, tier):
    checked(bundle)
    if tier != 'short': raise Failure('expansion_unavailable',409)
    text = bundle['decision']['text']; refs = evidence(bundle)
    return {'status':'source_only','short_answer':text,'answer_md':text,'display_blocks':[{'block_id':'guidance','text':text,'evidence_ids':[r['evidence_id'] for r in refs]}],
            'evidence':refs,'source_badges':list({r['source_slug']:{'source_slug':r['source_slug'],'label':r['source_label']} for r in refs}.values()),
            'answer_kind':'deterministic_guidance','answer_support':'supported' if refs else 'clarification', 'generation_status':'not_generated',
            'diagnostics':{'writer':{'status':'not_invoked','reason':bundle['decision']['kind']},'request_interpretation':{'kind':bundle['decision']['kind'],'original_question':bundle['question'],'explicit_context':bundle['context']}},
            'deterministic_result':deepcopy(bundle['decision']), 'limitations':[], 'synthesis_mode':'deterministic_guidance'}

def source(bundle, eid, tier):
    if tier != 'short': raise Failure('not_found',404)
    for r, e in zip(bundle['decision']['resources'], evidence(bundle)):
        if e['evidence_id'] == eid:
            return {**e,'excerpt':r['title']+'\n'+r.get('preview',r['url']),'bibliography':{'title':r['title'],'publisher':r.get('source_label','Jezik na klik'),'date':r['verified_at']},'edition':r['verified_at']}
    raise Failure('not_found',404)

def matches(bundle, offset=0, limit=20, source=None, summary=False):
    rows=[{**e,'record_id':e['evidence_id'],'evidence_ids':[e['evidence_id']],'preview':r.get('preview',e['url']),'snippet':r.get('preview',e['url']),'used_in_answer':True} for e,r in zip(evidence(bundle),bundle['decision']['resources'])]
    if summary:
        slugs = list(dict.fromkeys(r['source_slug'] for r in rows))
        return {'sources':[{'source_slug':s,'source_label':next(r['source_label'] for r in rows if r['source_slug']==s),'returned_count':sum(r['source_slug']==s for r in rows)} for s in slugs],'returned_count':len(rows),'count_scope':'returned_unique_records','usage_final':True}
    if source is not None: rows=[r for r in rows if r['source_slug']==source]
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise Failure('invalid_request')
    return {'items':rows[offset:offset+limit],'offset':offset,'limit':limit,'returned_count':len(rows),'next_offset':offset+limit if offset+limit<len(rows) else None,'usage_final':True,'count_scope':'returned_unique_records','corpus_total':None,'source_slug':source}
