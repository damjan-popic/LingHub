"""Frozen reader facets, independent of today's analyzers and writer availability."""
from copy import deepcopy

GRAMMAR={
 'sl':{'nominative':'imenovalnik','genitive':'rodilnik','dative':'dajalnik','accusative':'tožilnik','locative':'mestnik','instrumental':'orodnik','singular':'ednina','dual':'dvojina','plural':'množina','proper':'lastno ime','common':'občno ime','masculine':'moški spol','feminine':'ženski spol','neuter':'srednji spol'},
 'it':{'nominative':'nominativo','genitive':'genitivo','dative':'dativo','accusative':'accusativo','locative':'locativo','instrumental':'strumentale','singular':'singolare','dual':'duale','plural':'plurale','proper':'nome proprio','common':'nome comune','masculine':'maschile','feminine':'femminile','neuter':'neutro'}}

def profile(bundle):
 capture=bundle.get('analysis')or{}
 if capture.get('version')!='klikai.lexical-collation.v1':return None
 return capture['captures'][0]['response']

def overview(bundle):
 p=profile(bundle)
 return bool(p and p['interpretation']['query_kind']in {'word','phrase','comparison'}and not {'translation','orthography'}&set(p['interpretation']['requested_facets'])and len({u['kind']for u in p['evidence']if u['match_relation']not in {'component_only','alternative_lexical_identity'}})>=2)

def reader(bundle):
 p=profile(bundle)
 if not p:return None
 it=bundle['locale']=='it';sections=[];labels=GRAMMAR['it'if it else'sl']
 def section(title,rows):
  if rows:sections.append({'title':title,'items':rows})
 def measures(items):
  return '; '.join(str(x['value'])+' · '+(x.get('corpus')or ('corpus non specificato'if it else'korpus ni naveden')) for x in items if x.get('value')is not None and x.get('measure')=='frequency')
 morphology=[]
 for part in p['facets']['morphology']['data']:
  for le in (part.get('result')or{}).get('lexemes',[]):
   grammar=le['grammatical_features'];freq=measures(le['frequency_observations'])
   rows=[];focus=p['interpretation']['slot_focus']
   for s in le['slots']:
    fs=s['normalized_features'];forms=list(dict.fromkeys(v['form']for v in s['variants']))
    label=' · '.join(labels.get(fs.get(k,''),fs.get(k,''))for k in ('case','number','gender')if fs.get(k))
    rows.append({'title':label,'text':' / '.join(forms),'label':'Sloleks 3.1','record_id':le['source_entry_id']})
   noteworthy=[r for r,s in zip(rows,le['slots'])if len({v['form']for v in s['variants']})>1 and (not focus or all(s['normalized_features'].get(k)==v for k,v in focus.items()if k in {'case','number'}and v))]
   morphology.append({'title':le['lemma']+' · '+', '.join(labels.get(v,v)for v in grammar.values()),'text':(('Frequenza del lessema: 'if it else'Pogostnost leksema: ')+freq if freq else ('Frequenza non registrata.'if it else'Pogostnost ni zapisana.')),'label':'Sloleks 3.1 · '+le['source_status'],'record_id':le['source_entry_id'],'items':noteworthy[:2],'forms':rows,'patterns':[{'title':pat['source_pattern_id'],'items':[{'title':rows[m['slot']]['title'],'text':le['slots'][m['slot']]['variants'][m['variant']]['form']}for m in pat['members']]}for pat in le['patterns']]})
 section('Forme e flessione'if it else'Oblike in pregibanje',morphology)
 synonyms=[];collocations=[];loris=[];terms=[]
 for u in p['evidence']:
  d=u['fields'];base={'record_id':u['record_id'],'label':{'synonyms':'Sopomenke 2.2','collocations':'Kolokacije 2.2','loris':'LORIS'}.get(u['kind'],u['provenance'].get('resource_id','')),'title':u['target']}
  if u['match_relation']=='alternative_lexical_identity':base['label']+=' · '+('altra voce lessicale'if it else'druga slovarska iztočnica')
  if u['match_relation']=='component_only':base['label']+=' · '+('solo componente'if it else'le sestavina')
  if u['kind']=='synonyms':
   sense=d['sense'];relations=sense.get('relations',[])
   synonyms.append({**base,'label':base['label']+' · '+str(d.get('head_status')or'—'),'title':d['headword']+' · '+str(sense['sense_id']),'text':' · '.join(str(r.get('target_headword')or r.get('raw_text')or r.get('text')or r.get('lemma')or r.get('synonym')or r.get('target')or r.get('target_lemma')or '')+' ('+({'synonym':'sinonimo'if it else'sopomenka','antonym':'contrario'if it else'protipomenka'}.get(r.get('relation_type'),r.get('relation_type')or'—')+(' · '+str(r['synonym_type'])if r.get('synonym_type')else''))+')'for r in relations)})
  elif u['kind']=='collocations':
   freq=d.get('frequency');collocations.append({**base,'title':d['surface'],'text':(('Frequenza: 'if it else'Pogostnost: ')+str(freq)+' · Gigafida 2.0'if freq is not None else ('Frequenza non registrata'if it else'Pogostnost ni zapisana'))+(' · logDice: '+str(d['logDice'])if d.get('logDice')is not None else'')+(' · '+('solo componente'if it else'le sestavina')if u['match_relation']=='component_only'else'')})
  elif u['kind']=='loris':
   payload=d.get('payload',{});loris.append({**base,'text':((' → '+payload['SteMislili']+'. ')if payload.get('SteMislili')else'')+str(payload.get('Vslo')or payload.get('explanation')or payload.get('message')or payload.get('description')or payload.get('sl')or payload.get('recommendation')or '')})
  elif u['kind']=='terminology':terms.append({**base,'title':d['title'],'text':' · '.join(d.get('standardized_terms',[])+d.get('proposals',[]))})
 section('Sinonimi per accezione'if it else'Sopomenke po pomenih',synonyms)
 section('Collocazioni registrate'if it else'Zapisane besedne zveze',collocations)
 section('LORIS',loris);section('Terminologia'if it else'Terminologija',terms)
 return {'title':'Profilo lessicale'if it else'Podatki o izrazu','sections':sections,'note':'Dati delle fonti, indipendenti dalla risposta generata. Le frequenze non stabiliscono una preferenza normativa.'if it else'Podatki iz virov, neodvisni od generiranega odgovora. Pogostnost ne določa normativne prednosti.'}
