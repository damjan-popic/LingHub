"""Frozen reader facets, independent of today's analyzers and writer availability."""
from copy import deepcopy

GRAMMAR={
 'sl':{'nominative':'imenovalnik','genitive':'rodilnik','dative':'dajalnik','accusative':'tožilnik','locative':'mestnik','instrumental':'orodnik','singular':'ednina','dual':'dvojina','plural':'množina','proper':'lastno ime','common':'občno ime','masculine':'moški spol','feminine':'ženski spol','neuter':'srednji spol'},
 'it':{'nominative':'nominativo','genitive':'genitivo','dative':'dativo','accusative':'accusativo','locative':'locativo','instrumental':'strumentale','singular':'singolare','dual':'duale','plural':'plurale','proper':'nome proprio','common':'nome comune','masculine':'maschile','feminine':'femminile','neuter':'neutro'}}
GRAMMAR['sl'].update(adjective='pridevnik',adverb='prislov',noun='samostalnik',verb='glagol',general='splošni',positive='osnovnik',comparative='primernik',superlative='presežnik',definite='določna oblika',indefinite='nedoločna oblika',yes='določna oblika',no='nedoločna oblika')
GRAMMAR['it'].update(adjective='aggettivo',adverb='avverbio',noun='sostantivo',verb='verbo',general='generale',positive='positivo',comparative='comparativo',superlative='superlativo',definite='forma definita',indefinite='forma indefinita',yes='forma definita',no='forma indefinita')
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
 def status_details(raw):return [{'title':'Stato originale'if it else'Izvorni status','text':str(raw or '—')}]
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
    label=' · '.join(labels.get(str(fs[k]),str(fs[k]))for k in ('case','number','gender','degree','definiteness','definite','animacy')if fs.get(k)is not None)
    rows.append({'title':label,'text':' / '.join(forms),'label':'Sloleks 3.1','record_id':le['source_entry_id']})
   noteworthy=[r for r,s in zip(rows,le['slots'])if len({v['form']for v in s['variants']})>1 and (not focus or all(s['normalized_features'].get(k)==v for k,v in focus.items()if k in {'case','number'}and v))]
   title=le['lemma']+' · '+', '.join(labels.get(v,v)for v in grammar.values())
   if decision:title=le['lemma']+' · '+labels.get(le['grammatical_type'],le['grammatical_type'])+(' · '+', '.join(labels.get(v,v)for v in grammar.values()if v!='general')if any(v!='general'for v in grammar.values())else'')
   row={'title':title,'text':(('Frequenza del lessema: 'if it else'Pogostnost leksema: ')+freq if freq else ('Frequenza non registrata.'if it else'Pogostnost ni zapisana.')),'label':'Sloleks 3.1 · '+(status_label(le['source_status'])if decision else le['source_status']),'details':status_details(le['source_status'])if decision else [],'record_id':le['source_entry_id'],'items':noteworthy[:2],'forms':rows,'patterns':[{'title':pat['source_pattern_id'],'items':[{'title':rows[m['slot']]['title'],'text':le['slots'][m['slot']]['variants'][m['variant']]['form']}for m in pat['members']]}for pat in le['patterns']]}
   (alternatives if state=='alternative'else morphology).append(row)
 section('Forme e flessione'if it else'Oblike in pregibanje',morphology)
 synonyms=[];collocations=[];loris=[];terms=[]
 for u in p['evidence']:
  if u.get('selection',{}).get('state')in {'excluded','alternative'}:continue
  d=u['fields'];base={'record_id':u['record_id'],'label':{'synonyms':'Sopomenke 2.2','collocations':'Kolokacije 2.2','loris':'LORIS'}.get(u['kind'],u['provenance'].get('resource_id','')),'title':u['target']}
  if u['match_relation']=='alternative_lexical_identity':base['label']+=' · '+('altra voce lessicale'if it else'druga slovarska iztočnica')
  if u['match_relation']=='component_only':base['label']+=' · '+('solo componente'if it else'le sestavina')
  if u['kind']=='synonyms':
   sense=d['sense'];relations=sense.get('relations',[])
   synonyms.append({**base,'label':base['label']+' · '+(status_label(d.get('head_status'))if decision else str(d.get('head_status')or'—')),'title':d['headword']+' · '+('gruppo di significato'if it else'pomenska skupina'),'text':' · '.join(str(r.get('target_headword')or r.get('raw_text')or '')+' ('+({'synonym':'sinonimo'if it else'sopomenka','antonym':'contrario'if it else'protipomenka'}.get(r.get('relation_type'),r.get('relation_type')or'—'))+')'for r in relations),'details':status_details(d.get('head_status'))+[{'title':'ID','text':str(sense['sense_id'])},{'title':'Relazioni originali'if it else'Izvorne oznake povezav','text':'; '.join(str(r.get('target_headword')or r.get('raw_text'))+': '+str(r.get('synonym_type')or r.get('relation_type'))for r in relations)}]})
  elif u['kind']=='collocations':
   if u.get('editorial_status',{}).get('raw'):base['label']+=' · '+status_label(u['editorial_status']['raw']);base['details']=status_details(u['editorial_status']['raw'])
   freq=d.get('frequency');collocations.append({**base,'title':d['surface'],'text':(('Frequenza della collocazione: 'if it else'Pogostnost kolokacije: ')+str(freq)+' · Gigafida 2.0'if freq is not None else ('Frequenza non registrata'if it else'Pogostnost ni zapisana'))+(' · logDice: '+str(d['logDice'])if d.get('logDice')is not None else'')+(' · '+('solo componente'if it else'le sestavina')if u['match_relation']=='component_only'else'')})
  elif u['kind']=='loris':
   payload=d.get('payload',{});loris.append({**base,'text':((' → '+payload['SteMislili']+'. ')if payload.get('SteMislili')else'')+str(payload.get('Vslo')or payload.get('explanation')or payload.get('message')or payload.get('description')or payload.get('sl')or payload.get('recommendation')or '')})
  elif u['kind']=='terminology':terms.append({**base,'title':d['title'],'text':' · '.join(d.get('standardized_terms',[])+d.get('proposals',[]))})
 section('Sinonimi per accezione'if it else'Sopomenke po pomenih',synonyms)
 section('Collocazioni registrate'if it else'Zapisane besedne zveze',collocations)
 section('LORIS',loris);section('Terminologia'if it else'Terminologija',terms)
 analysis_rows=[]
 for u in p['evidence']:
  if u['kind']!='analysis' or u.get('selection',{}).get('state')!='primary':continue
  for engine,data in u['fields']['engines'].items():
   for token in data['tokens']:
    fs=token.get('feats')or{};analysis_rows.append({'title':token['text']+' · '+engine,'text':token['lemma']+' · '+', '.join(labels.get({'Nom':'nominative','Gen':'genitive','Dat':'dative','Acc':'accusative','Loc':'locative','Ins':'instrumental','Sing':'singular','Dual':'dual','Plur':'plural'}.get(str(v),str(v)),str(v))for k,v in fs.items()),'label':'Analisi contestuale automatica'if it else'Samodejna kontekstualna analiza','record_id':u['record_id']})
  for form,data in u['fields']['recorded_forms'].items():
   readings=[r for r in data['recorded_readings']if r.get('selection')!='alternative']
   if readings:analysis_rows.append({'title':form+' · Sloleks','text':'; '.join(dict.fromkeys(r['lemma']+' · '+', '.join(labels.get(str(v),str(v))for k,v in r['features'].items()if k in {'case','number','gender','degree'})for r in readings)),'label':'Letture registrate; non disambiguazione contestuale'if it else'Zapisane slovnične možnosti; ne razdvoumljenje v sobesedilu','record_id':u['record_id']})
 section('Analisi della forma inserita'if it else'Analiza vnesene oblike',analysis_rows)
 section('Altre analisi automatiche registrate'if it else'Druge samodejno zapisane analize',alternatives)
 return {'title':'Profilo lessicale'if it else'Podatki o izrazu','sections':sections,'note':'Dati delle fonti, indipendenti dalla risposta generata. Le frequenze non stabiliscono una preferenza normativa.'if it else'Podatki iz virov, neodvisni od generiranega odgovora. Pogostnost ne določa normativne prednosti.'}
