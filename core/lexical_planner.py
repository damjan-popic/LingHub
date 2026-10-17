"""Original-span lexical planning from structure, cues and both raw analyses."""
import re,unicodedata
VERSION='lexical-target-spans-v1'
WORD=re.compile(r"[^\W\d_]+(?:[\u0300-\u036f]*[^\W\d_]*)(?:[-’'][^\W\d_]+)*",re.UNICODE)
FACETS={'synonyms':r'sopomen|sinonim','morphology':r'sklanj|pregib|rodilnik|imenovalnik|dvojin|množin|sklon','collocations':r'kolokacij|besedn\w* zvez|collocaz','frequency':r'pogost|frekvenc','orthography':r'začetnic|pravopis|piš|zapiš','translation':r'prevod|preved|italijanščin|slovenščin|ustreznic'}
SCAFFOLD={'kaj','kako','kateri','katera','katere','katero','katerih','kdo','ali','je','so','sta','se','v','za','beseda','besede','besedo','besedi','prav','pravilno','prosim','katerega','pomeni','pomen','oblika','oblike','obliko','primer','napiše','reče','naj','mi','razloži','kakšen','kakšna','katere','danes','ni','brez'}
def lexical_word(text):
 t=text.strip().strip('"«»“”‘’?!.,;:');return bool(WORD.fullmatch(t))and not any(c.isdigit()for c in t)
def plan(text,context,language,analysis):
 words=list(WORD.finditer(text));requested=[k for k,p in FACETS.items()if re.search(p,text,re.I)]
 hints=[w for r in analysis.get('raw_engines',{}).values()for w in r.get('analysis',{}).get('words',[])];spans=[];kind='question';basis=[]
 def add(a,b,why):
  if any(m.start()<b and a<m.end() for m in re.finditer(r'https?://\S+|\b\w*[0-9_]\w*\b',text)):return
  while a<b and text[a].isspace():a+=1
  while b>a and text[b-1]in' \n\t?.!,;:':b-=1
  if a<b and not any(s['start']==a and s['end']==b for s in spans):spans.append({'start':a,'end':b,'text':text[a:b],'basis':why})
 if lexical_word(text):
  m=WORD.search(text);add(m.start(),m.end(),'single lexical surface');kind='word'
 elif len(words)<=8 and not requested and words and words[0].group().casefold()not in SCAFFOLD and '?'not in text:
  add(0,len(text),'bounded whole phrase before component lookup');kind='phrase'
 else:
  for m in re.finditer(r'«([^»]+)»|»([^«]+)«|“([^”]+)”|"([^"]+)"',text):
   gi=next(i for i,v in enumerate(m.groups(),1)if v is not None);add(*m.span(gi),'quoted original span')
  # Comparative alternatives stay separate and retain spelling, casing and case context.
  if not spans and re.search(r'\bali\b',text,re.I):
   pieces=[];begin=0
   for boundary in list(re.finditer(r'\bali\b',text,re.I))+[None]:
    stop=boundary.start()if boundary else len(text)
    content=[w for w in words if begin<=w.start()<stop and w.group().casefold()not in SCAFFOLD and not any(re.search(p,w.group(),re.I)for p in FACETS.values())]
    if content and len(content)<=4:pieces.append((content[0].start(),content[-1].end()))
    begin=boundary.end()if boundary else len(text)
   if len(pieces)>=2:
    for a,b in pieces:add(a,b,'comparison alternative; whole original phrase')
    kind='comparison'
  if not spans:
   cue=re.search(r'\b(?:besed[aeoiu]|izraza?|imena?|termina?)\s+(.+)',text,re.I)
   if cue:add(cue.start(1),cue.end(1),'requested lexical object')
  if not spans:
   # Statistical structure nominates content spans; lookup verifies them later.
   candidates=[w for w in hints if w.get('upos')in {'NOUN','PROPN','ADJ','VERB'}and w.get('start')is not None and w['text'].casefold()not in SCAFFOLD and not any(re.search(p,w['text'],re.I)for p in FACETS.values())]
   for w in candidates[-4:]:add(w['start'],w['end'],'analyzer content span')
  if not spans and len(words)<=8 and words:
   for w in words:
    if w.group().casefold()not in SCAFFOLD and not any(re.search(p,w.group(),re.I)for p in FACETS.values()):add(w.start(),w.end(),'degraded lexical candidate; source match required')
 if len(spans)>1 and len({s['text'].casefold()for s in spans})==1 and len({s['text']for s in spans})>1 and 'orthography'not in requested:requested.append('orthography')
 langs=[language]if language in {'sl','it'}else ['sl','it']
 # Direction cues describe target policy, not certain input-language detection.
 if language=='und'and re.search(r'\b(?:besed|sopomen|sklanj|rodilnik|kako|kaj|kdo|kateri|ali|brez|danes ni)',text,re.I):langs=['sl']
 for s in spans:
  s['lemma_candidates']=list(dict.fromkeys(w['lemma']for w in hints if w.get('start')is not None and s['start']<=w['start']and w['end']<=s['end']and w['lemma']))[:6]
  s['lemma_sequences']=list(dict.fromkeys(' '.join(w['lemma']for w in r.get('analysis',{}).get('words',[])if w.get('start')is not None and s['start']<=w['start']and w['end']<=s['end'])for r in analysis.get('raw_engines',{}).values()))
  s['context_features']=[{'engine':name,'feats':w['feats'],'upos':w['upos']}for name,r in analysis.get('raw_engines',{}).items()for w in r.get('analysis',{}).get('words',[])if w.get('start')is not None and s['start']<=w['start']and w['end']<=s['end']]
 case='genitive'if re.search(r'\b(?:brez|rodilnik\w*|ni)\b',text,re.I)else'nominative'if re.search(r'\b(?:imenovalnik\w*|kdo je)\b',text,re.I)else None
 return {'version':VERSION,'query_kind':kind,'original':text,'context':context,'targets':spans[:4],'targets_truncated':len(spans)>4,'requested_facets':requested,'inferred_facets':['loris','morphology','frequency','synonyms','collocations','terminology'],'lookup_languages':langs,'language_basis':'explicit'if language!='und'else'bounded inventory/wording policy, not certain language detection','slot_focus':{'case':case,'number':'singular'if case and not re.search(r'dvojin|množin',text,re.I)else None,'basis':'grammatical question/context cue; verify against source slot'}if case else None,'interpretation_uncertainty':'multiple resource identities and senses remain separate; no automatic normative verdict','basis':['original surface','question structure','raw analyzer content spans','resource validation follows']}
