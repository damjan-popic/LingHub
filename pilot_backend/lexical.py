"""Frozen reader facets, independent of today's analyzers and writer availability."""
from copy import deepcopy

GRAMMAR={
 'sl':{'nominative':'imenovalnik','genitive':'rodilnik','dative':'dajalnik','accusative':'tožilnik','locative':'mestnik','instrumental':'orodnik','singular':'ednina','dual':'dvojina','plural':'množina','proper':'lastno ime','common':'občno ime','masculine':'moški spol','feminine':'ženski spol','neuter':'srednji spol'},
 'it':{'nominative':'nominativo','genitive':'genitivo','dative':'dativo','accusative':'accusativo','locative':'locativo','instrumental':'strumentale','singular':'singolare','dual':'duale','plural':'plurale','proper':'nome proprio','common':'nome comune','masculine':'maschile','feminine':'femminile','neuter':'neutro'}}
GRAMMAR['sl'].update(adjective='pridevnik',adverb='prislov',noun='samostalnik',verb='glagol',general='splošni',positive='osnovnik',comparative='primernik',superlative='presežnik',definite='določna oblika',indefinite='nedoločna oblika',yes='določna oblika',no='nedoločna oblika')
GRAMMAR['it'].update(adjective='aggettivo',adverb='avverbio',noun='sostantivo',verb='verbo',general='generale',positive='positivo',comparative='comparativo',superlative='superlativo',definite='forma definita',indefinite='forma indefinita',yes='forma definita',no='forma indefinita')
GRAMMAR['sl'].update(animate='živo',inanimate='neživo',main='polnopomenski',auxiliary='pomožni',perfective='dovršni',progressive='nedovršni',biaspectual='dvovidski')
GRAMMAR['it'].update(animate='animato',inanimate='inanimato',main='lessicale',auxiliary='ausiliare',perfective='perfettivo',progressive='imperfettivo',biaspectual='biaspettuale')
for language in GRAMMAR:
 for short,full in {'Pos':'positive','Cmp':'comparative','Sup':'superlative','Masc':'masculine','Fem':'feminine','Neut':'neuter'}.items():GRAMMAR[language][short]=GRAMMAR[language][full]

def profile(bundle):
 capture=bundle.get('analysis')or{}
 if capture.get('version')!='klikai.lexical-collation.v1':return None
 return capture['captures'][0]['response']

def overview(bundle):
 p=profile(bundle)
 return bool(p and p['interpretation']['query_kind']in {'word','phrase','comparison'}and not {'translation','orthography'}&set(p['interpretation']['requested_facets'])and len({u['kind']for u in p['evidence']if u['kind']!='analysis'and u.get('selection',{}).get('state','primary')=='primary'and u['match_relation']not in {'component_only','alternative_lexical_identity'}})>=2)

def reader(bundle):
 p=profile(bundle)
 if not p:return None
 it=bundle['locale']=='it';sections=[];alternatives=[];labels=GRAMMAR['it'if it else'sl']
 decision=p['interpretation'].get('selection');by_id={}
 for u in p['evidence']:
  if u['kind']=='morphology':by_id[u['entry_id'].split(':slot:')[0]]=u.get('selection',{})
 def status_label(raw):
  value=str(raw or '').lower()
  if value in {'manual','ssss-manual'}:return 'revisione manuale'if it else'ročno pregledano'
  if value in {'automatic','ssss-automatic'}:return 'elaborazione automatica'if it else'samodejno pripravljeno'
  return 'stato non specificato'if it else'status ni opredeljen'
 def status_details(raw):return [{'title':'Preparazione dei dati'if it else'Priprava podatkov','text':status_label(raw)}]
 def section(title,rows):
  if rows:sections.append({'title':title,'items':rows})
 def measures(items):
  return '; '.join(str(x['value'])+' · '+(x.get('corpus')or ('corpus non specificato'if it else'korpus ni naveden')) for x in items if x.get('value')is not None and x.get('measure')=='frequency')
 morphology=[]
 for part in p['facets']['morphology']['data']:
  for le in (part.get('result')or{}).get('lexemes',[]):
   state=by_id.get(le['source_entry_id'],{}).get('state','primary')
   if decision and state=='excluded':continue
   if any(r['record_id']==le['source_entry_id']for r in morphology+alternatives):continue
   grammar=le['grammatical_features'];freq=measures(le['frequency_observations'])
   rows=[];focus=p['interpretation']['slot_focus']
   for s in le['slots']:
    fs=s['normalized_features'];forms=list(dict.fromkeys(v['form']for v in s['variants']))
    label=' · '.join(labels[str(fs[k])]for k in ('case','number','gender','degree','definiteness','definite','animacy')if str(fs.get(k))in labels)
    rows.append({'title':label,'text':' / '.join(forms),'label':'Sloleks 3.1','record_id':le['source_entry_id']})
   noteworthy=[r for r,s in zip(rows,le['slots'])if len({v['form']for v in s['variants']})>1 and (not focus or all(s['normalized_features'].get(k)==v for k,v in focus.items()if k in {'case','number'}and v))]
   title=le['lemma']+' · '+', '.join(labels[v]for v in grammar.values()if v in labels)
   if decision:title=le['lemma']+' · '+labels.get(le['grammatical_type'],le['grammatical_type'])+(' · '+', '.join(labels[v]for v in grammar.values()if v!='general'and v in labels)if any(v!='general'for v in grammar.values())else'')
   row={'title':title,'text':(('Frequenza del lessema: 'if it else'Pogostnost leksema: ')+freq if freq else ('Frequenza non registrata.'if it else'Pogostnost ni zapisana.')),'label':'Sloleks 3.1 · '+status_label(le['source_status']),'details':status_details(le['source_status']),'record_id':le['source_entry_id'],'items':noteworthy[:2],'forms':rows,'patterns':[{'title':('Schema di flessione 'if it else'Vzorec pregibanja ')+str(number+1),'items':[{'title':rows[m['slot']]['title'],'text':le['slots'][m['slot']]['variants'][m['variant']]['form']}for m in pat['members']]}for number,pat in enumerate(le['patterns'])]}
   (alternatives if state=='alternative'else morphology).append(row)
 section('Forme e flessione'if it else'Oblike in pregibanje',morphology)
 synonyms=[];collocations=[];loris=[];terms=[]
 for u in p['evidence']:
  if u.get('selection',{}).get('state')in {'excluded','alternative'}:continue
  d=u['fields'];base={'record_id':u['record_id'],'label':{'synonyms':'Sopomenke 2.2','collocations':'Kolokacije 2.2','loris':'LORIS'}.get(u['kind'],'Terminologia'if it else'Terminološki vir'),'title':u['target']}
  if u['match_relation']=='alternative_lexical_identity':base['label']+=' · '+('altra voce lessicale'if it else'druga slovarska iztočnica')
  if u['match_relation']=='component_only':base['label']+=' · '+('solo componente'if it else'le sestavina')
  if u['kind']=='synonyms':
   sense=d['sense'];relations=sense.get('relations',[])
   def relation_label(relation):
    if relation.get('relation_type')=='antonym':return 'contrario'if it else'protipomenka'
    if relation.get('relation_type')=='synonym':
     if relation.get('synonym_type')in {'near','near-synonym','near_synonym'}:return 'sinonimo vicino'if it else'bližnja sopomenka'
     return 'sinonimo'if it else'sopomenka'
    return 'relazione di significato'if it else'pomenska povezava'
   synonyms.append({**base,'label':base['label']+' · '+status_label(d.get('head_status')),'title':d['headword']+' · '+('gruppo di significato'if it else'pomenska skupina'),'text':' · '.join(str(r.get('target_headword')or r.get('raw_text')or '')+' ('+relation_label(r)+')'for r in relations),'details':status_details(d.get('head_status'))})
  elif u['kind']=='collocations':
   if u.get('editorial_status',{}).get('raw'):base['label']+=' · '+status_label(u['editorial_status']['raw']);base['details']=status_details(u['editorial_status']['raw'])
   freq=d.get('frequency');collocations.append({**base,'title':d['surface'],'text':(('Frequenza della collocazione: 'if it else'Pogostnost kolokacije: ')+str(freq)+' · Gigafida 2.0'if freq is not None else ('Frequenza non registrata'if it else'Pogostnost ni zapisana'))+(' · logDice: '+str(d['logDice'])if d.get('logDice')is not None else'')+(' · '+('solo componente'if it else'le sestavina')if u['match_relation']=='component_only'else'')})
  elif u['kind']=='loris':
   payload=d.get('payload',{});loris.append({**base,'text':((' → '+payload['SteMislili']+'. ')if payload.get('SteMislili')else'')+str(payload.get('Vslo')or payload.get('explanation')or payload.get('message')or payload.get('description')or payload.get('sl')or payload.get('recommendation')or '')})
  elif u['kind']=='terminology':terms.append({**base,'title':d['title'],'text':' · '.join(d.get('standardized_terms',[])+d.get('proposals',[]))})
 section('Relazioni di significato'if it else'Pomenske povezave',synonyms)
 section('Collocazioni registrate'if it else'Zapisane besedne zveze',collocations)
 section('LORIS',loris);section('Terminologia'if it else'Terminologija',terms)
 interpretation_rows=[];analysis_rows=[];uncertainty=[]
 for u in p['evidence']:
  if u['kind']!='analysis' or u.get('selection',{}).get('state','primary')!='primary':continue
  fields=u['fields'];facts=fields.get('supported_derivations',[])
  for fact in facts:
   if fact.get('fact_class')!='supported_application':continue
   if it:
    readings=fact.get('readings',[])
    if fact.get('status')=='supported'and len(readings)==1:
     reading=readings[0];text='Nella normale costruzione aggettivo–nome, «'+fact['construction']['text']+'» ha una lettura '+', '.join(labels[v]for k,v in reading.items()if k in {'case','number','gender'}and v in labels)+'. Le forme registrate concordano in genere, numero e caso.'
    elif fact.get('status')=='incompatible':text='Le forme registrate non concordano in genere, numero e caso nella normale costruzione aggettivo–nome; per un’altra funzione serve il contesto.'
    else:text='Le forme registrate ammettono più letture concordanti; il contesto determina la scelta.'
   else:text=fact.get('text_sl','')
   if text:interpretation_rows.append({'title':fact.get('construction',{}).get('text',u['target']),'text':text,'label':'Sloleks 3.1 · '+('accordo grammaticale'if it else'slovnično ujemanje'),'record_id':u['record_id']})
  for form,data in fields.get('recorded_forms',{}).items():
   readings=[r for r in data.get('recorded_readings',[])if r.get('selection')!='alternative']
   # Keep lexemes and grammatical readings distinct; normal cards never recite
   # engine names or replace lexical ambiguity with a model disagreement.
   if readings:analysis_rows.append({'title':form,'text':'; '.join(dict.fromkeys(r['lemma']+' · '+', '.join(labels[str(v)]for k,v in r['features'].items()if k in {'case','number','gender','degree'}and str(v)in labels)for r in readings)),'label':'Sloleks 3.1','record_id':u['record_id']})
  if fields.get('unresolved_inferences')or not facts:
   uncertainty.append({'title':fields.get('submitted_expression',u['target']),'text':'Le forme disponibili non determinano da sole il rapporto tra le parole; serve il contesto.'if it else'Razpoložljive oblike same ne določajo razmerja med besedami; za izbiro branja je potreben kontekst.','label':'Interpretazione aperta'if it else'Odprta razlaga','record_id':u['record_id']})
 section('Interpretazione della locuzione'if it else'Razlaga besedne zveze',interpretation_rows)
 section('Letture grammaticali registrate'if it else'Zapisane slovnične možnosti',analysis_rows)
 section('Contesto necessario'if it else'Potreben kontekst',uncertainty)
 section('Altre analisi automatiche registrate'if it else'Druge samodejno zapisane analize',alternatives)
 return {'title':'Profilo lessicale'if it else'Podatki o izrazu','sections':sections,'note':'Dati delle fonti, indipendenti dalla risposta generata. Le frequenze non stabiliscono una preferenza normativa.'if it else'Podatki iz virov, neodvisni od generiranega odgovora. Pogostnost ne določa normativne prednosti.'}
