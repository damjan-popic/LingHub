"""Original-span lexical planning from structure, cues and both raw analyses."""
import copy,re,unicodedata
VERSION='lexical-target-spans-v4'
REQUEST_VERSION='klikai.request-plan.v1'
WORD=re.compile(r"[^\W\d_]+(?:[\u0300-\u036f]*[^\W\d_]*)(?:[-’'][^\W\d_]+)*",re.UNICODE)
SL_INFLECTION_ACTION=r'sklanj\w*|pregib\w*'
IT_INFLECTION_ACTION=r'declin\w*|flett\w*'
INFLECTION_CUE=SL_INFLECTION_ACTION+'|'+IT_INFLECTION_ACTION+r'|flession\w*|coniug\w*'
FACETS={'synonyms':r'sopomen|sinonim','morphology':INFLECTION_CUE+r'|rodilnik|imenovalnik|dajalnik|tožilnik|mestnik|orodnik|nominativ|genitiv|dativ|accusativ|locativ|strumental|ednin|dvojin|množin|singolar|plural|sklon','collocations':r'kolokacij|besedn\w* zvez|collocaz','frequency':r'pogost|frekvenc','orthography':r'začetnic|pravopis|piš|zapiš','translation':r'prevod|preved|prevaj|italijanščin|slovenščin|ustreznic|traduc|traduz|(?:pove|povemo|reče|rečemo)\s+(?:po\s+)?(?:slovensko|italijansko)','meaning':r'\bkaj\s+(?:je|pomeni)\b'}
GRAMMAR_CASES=(
 ('nominative',r'\b(?:imenovalnik\w*|nominativ\w*)\b'),
 ('genitive',r'\b(?:rodilnik\w*|genitiv\w*)\b'),
 ('dative',r'\b(?:dajalnik\w*|dativ\w*)\b'),
 ('accusative',r'\b(?:tožilnik\w*|accusativ\w*)\b'),
 ('locative',r'\b(?:mestnik\w*|locativ\w*)\b'),
 ('instrumental',r'\b(?:orodnik\w*|strumental\w*)\b'),
)
GRAMMAR_NUMBERS=(
 ('singular',r'\b(?:ednin\w*|singolar\w*)\b'),
 ('dual',r'\bdvojin\w*\b'),
 ('plural',r'\b(?:množin\w*|plural\w*)\b'),
)
GRAMMAR_CASE_NAMES={
 'imenovalnik':'nominative','nominativo':'nominative',
 'rodilnik':'genitive','genitivo':'genitive',
 'dajalnik':'dative','dativo':'dative',
 'tožilnik':'accusative','accusativo':'accusative',
 'mestnik':'locative','locativo':'locative',
 'orodnik':'instrumental','strumentale':'instrumental'}
GRAMMAR_NUMBER_NAMES={
 'ednine':'singular','singolare':'singular',
 'dvojine':'dual','množine':'plural','plurale':'plural'}
_SL_CASE_TOKEN=r'(?:imenovalnik|rodilnik|dajalnik|tožilnik|mestnik|orodnik)'
_IT_CASE_TOKEN=r'(?:nominativo|genitivo|dativo|accusativo|locativo|strumentale)'
_SL_CASE_SURFACE=r'(?:imenovalnik\w*|rodilnik\w*|dajalnik\w*|tožilnik\w*|mestnik\w*|orodnik\w*)'
_SL_NUMBER_TOKEN=r'(?:ednine|dvojine|množine)'
_IT_NUMBER_TOKEN=r'(?:singolare|plurale)'
_SL_SLOT=rf'{_SL_CASE_TOKEN}(?:\s+{_SL_NUMBER_TOKEN})?'
_IT_SLOT=rf'(?:il\s+)?{_IT_CASE_TOKEN}(?:\s+{_IT_NUMBER_TOKEN})?'
_SL_INFLECTION_MODAL=r'(?:naj|bi|lahko)'
_SL_INFLECTION_MODIFIER=r'(?:naj|bi|lahko|pravilno|v\s+slovenščini)'
_NAME_DESCRIPTOR=re.compile(
 r'(?:[^\W\d_]+(?:sk|šk|čk)(?:i|a|o|e|ega|emu|em|im|ih|ima|imi)|'
 r'slovensk\w*|francosk\w*|italijansk\w*|mošk\w*|žensk\w*|'
 r'osebn\w*|tuj\w*|lastn\w*|rojstn\w*|krstn\w*|domač\w*)',re.I)
_SL_NAMED_ROLE=rf'(?:(?:{_NAME_DESCRIPTOR.pattern})\s+){{0,4}}(?:ime|priimek|besed[ao]|samostalnik|izraz)'
_SL_GENITIVE_NAMED_ROLE=rf'(?:(?:{_NAME_DESCRIPTOR.pattern})\s+){{0,4}}(?:imena|priimka|besede|samostalnika|izraza)'
_GRAMMAR_REQUESTS=(
 ('sl',re.compile(
  rf'\s*(?:(?:kako\s+se\s+(?:glasi|glasita|glasijo)|kakšen\s+je)\s+)?'
  rf'(?P<grammar>{_SL_SLOT}(?:\s+(?:ali|in|ter)\s+(?:{_SL_SLOT}|{_SL_NUMBER_TOKEN}))*)'
  rf'\s+(?:{_SL_GENITIVE_NAMED_ROLE}\s+)?'
  rf'(?P<target>.+?)\s*[.?!]*\s*$',re.I)),
 ('it',re.compile(
  rf'\s*(?:(?:qual\s+[èe]|quali\s+sono)\s+(?:il\s+)?)?'
  rf'(?P<grammar>{_IT_SLOT}(?:\s+(?:oppure|o|e|ed)\s+(?:{_IT_SLOT}|{_IT_NUMBER_TOKEN}))*)'
  rf'\s+(?:(?:del|della|dei|degli|delle)\s+'
  rf'(?:nome|cognome|parola|sostantivo|termine)\s+)?'
  rf'(?P<target>.+?)\s*[.?!]*\s*$',re.I)),
)

def _validated_grammar_target(text,language,start,end):
 while end>start and text[end-1]in' \t\n.?!':end-=1
 pairs={'«':'»','»':'«','“':'”','„':'“','"':'"',"'":"'"}
 if start<end and text[start]in pairs and text[end-1]==pairs[text[start]]:
  start+=1;end-=1
 target=text[start:end];words=[row.group().casefold()for row in WORD.finditer(target)]
 grammar_tokens=set(GRAMMAR_CASE_NAMES)|set(GRAMMAR_NUMBER_NAMES)
 connectors={'ali','in','ter'}if language=='sl'else{'e','ed','o','oppure'}
 role_only=(r'(?:imena|priimka|besede|samostalnika|izraza)'if language=='sl'
  else r'(?:(?:del|della|dei|degli|delle)\s+)?'
       r'(?:nome|cognome|parola|sostantivo|termine)')
 if words and words[0]in grammar_tokens:
  return None,'grammar_slot_pairing_unresolved'
 if (not target or len(words)>8 or set(words)&connectors or re.search(r'[;\n]',target)):
  return None,'grammar_target_binding_unresolved'
 if re.fullmatch(role_only,target,re.I):
  return None,'grammar_target_binding_unresolved'
 return (start,end),None

def _grammar_object_target(text,language):
 if language=='sl':
  match=re.search(r'\b(?:imena|priimka|besede|samostalnika|izraza)\s+'
                  r'(?P<target>[^?!.;\n]{1,160})\s*[?!.]*\s*$',text,re.I)
 else:
  match=re.search(r'\b(?:del|della|dei|degli|delle)\s+'
                  r'(?:nome|cognome|parola|sostantivo|termine)\s+'
                  r'(?P<target>[^?!.;\n]{1,160})\s*[?!.]*\s*$',text,re.I)
 return (_validated_grammar_target(text,language,*match.span('target'))
         if match else(None,'grammar_target_binding_unresolved'))

def _grammar_pairing_failure(text,language,target_span=None):
 """Keep an independently bounded target while failing ambiguous slot pairing."""
 if target_span is None:
  target,ambiguity=_grammar_object_target(text,language)
 else:
  target,ambiguity=_validated_grammar_target(text,language,*target_span)
  if ambiguity=='grammar_slot_pairing_unresolved':
   target,ambiguity=_grammar_object_target(text,language)
 if target is None:
  return {'matched':True,'slots':[],'target':None,
   'ambiguity':'grammar_target_binding_unresolved'}
 return {'matched':True,'slots':[],'target':None,
  'ambiguity':'grammar_slot_pairing_unresolved'}

def _grammar_value(surface,inventory):
 return next((name for name,pattern in inventory
              if surface and re.fullmatch(pattern,surface,re.I)),None)

def _grammar_inventory_pattern(inventory):
 patterns=[]
 for _,pattern in inventory:
  if pattern.startswith(r'\b')and pattern.endswith(r'\b'):
   pattern=pattern[2:-2]
  patterns.append(pattern)
 return '(?:'+'|'.join(patterns)+')'

def _grammar_slot_sequence(segment,language):
 """Parse ordered case/number pairs without inventing a cross-product."""
 italian=language=='it'
 case_word=_grammar_inventory_pattern(GRAMMAR_CASES)
 number_word=_grammar_inventory_pattern(GRAMMAR_NUMBERS)
 article=r'(?:(?:il|lo|la)\s+)?'if italian else''
 slot=re.compile(
  rf'\s*{article}(?P<case>{case_word})'
  rf'(?:\s+(?P<number>{number_word}))?\s*',re.I)
 number_only=re.compile(rf'\s*(?P<number>{number_word})\s*',re.I)
 separator=re.compile(
  r'\s*(?:,|\b(?:e|ed)\b)\s*'if italian else
  r'\s*(?:,|\b(?:in|ter)\b)\s*',re.I)
 if re.search(r'\b(?:o|oppure)\b'if italian else r'\bali\b',segment,re.I):
  return [],True
 slots=[];shorthand=False;position=0
 while position<len(segment):
  match=slot.match(segment,position)
  if match is None:return [],True
  slots.append({'case':_grammar_value(match['case'],GRAMMAR_CASES),
   'number':_grammar_value(match['number'],GRAMMAR_NUMBERS)})
  position=match.end()
  if position==len(segment):break
  connector=separator.match(segment,position)
  if connector is None or connector.end()==len(segment):return [],True
  position=connector.end()
  if slot.match(segment,position)is not None:continue
  number=number_only.match(segment,position)
  if number is None or slots[-1]['number']is None:return [],True
  shorthand=True
  slots.append({'case':slots[-1]['case'],
                'number':_grammar_value(number['number'],GRAMMAR_NUMBERS)})
  position=number.end()
  if position==len(segment):break
  connector=separator.match(segment,position)
  if connector is None or connector.end()==len(segment):return [],True
  position=connector.end()
 identities=[(row['case'],row['number'])for row in slots]
 if (not slots or len(slots)>6 or len(identities)!=len(set(identities))
     or shorthand and len({row['case']for row in slots})!=1
     or any(row['case']is None for row in slots)):
  return [],True
 return slots,False

def structured_grammar_request(text):
 """Parse ordered case/number pairs and their one exact lexical target."""
 postfix=re.fullmatch(
  rf'\s*kako\s+(?:(?:{_SL_INFLECTION_MODAL})\s+)?(?:se\s+)?'
  rf'(?:(?:{_SL_INFLECTION_MODIFIER})\s+){{0,3}}'
  rf'(?:{SL_INFLECTION_ACTION})\s+(?P<label>{_SL_NAMED_ROLE})\s+'
  rf'(?P<target>[^?!.;\n]{{1,160}}?)\s+v\s+'
  rf'(?P<grammar>{_SL_CASE_SURFACE}[^?!.;\n]{{0,120}})\s*[.?!]*\s*$',text,re.I)
 if postfix:
  target,ambiguity=_validated_grammar_target(
   text,'sl',*postfix.span('target'))
  if ambiguity:
   return {'matched':True,'slots':[],'target':None,'ambiguity':ambiguity}
  slots,invalid=_grammar_slot_sequence(postfix['grammar'],'sl')
  if invalid:
   return {'matched':True,'slots':[],'target':None,
           'ambiguity':'grammar_slot_pairing_unresolved'}
  return {'matched':True,'slots':slots,
          'target':target,'ambiguity':None}
 wrappers=(
  re.compile(r'\s*(?:kako\s+se\s+(?:glasi|glasita|glasijo)\s+)?'
             rf'(?:{_SL_CASE_TOKEN})\b',re.I),
  re.compile(r'\s*(?:(?:qual\s+[èe]|quali\s+sono)\s+(?:il\s+)?)?'
             rf'(?:il\s+)?(?:{_IT_CASE_TOKEN})\b',re.I))
 candidate=any(pattern.match(text)for pattern in wrappers)
 for language,pattern in _GRAMMAR_REQUESTS:
  match=pattern.fullmatch(text)
  if not match:continue
  slots,invalid=_grammar_slot_sequence(match['grammar'],language)
  if invalid:
   return _grammar_pairing_failure(text,language,match.span('target'))
  target,ambiguity=_validated_grammar_target(
   text,language,*match.span('target'))
  if ambiguity=='grammar_slot_pairing_unresolved':
   return {'matched':True,'slots':[],'target':None,'ambiguity':ambiguity}
  return {'matched':True,'slots':slots,'target':target,'ambiguity':ambiguity}
 if candidate:
  language='it'if re.search(_IT_CASE_TOKEN,text,re.I)else'sl'
  return _grammar_pairing_failure(text,language)
 return {'matched':False,'slots':[],'target':None,'ambiguity':None}

def explicit_grammar_slots(text):
 """Return every current-utterance slot in textual order, without guessing."""
 def collect(patterns):
  found=sorted((match.start(),name)for name,pattern in patterns
               for match in re.finditer(pattern,text,re.I))
  return list(dict.fromkeys(name for _,name in found))
 return collect(GRAMMAR_CASES),collect(GRAMMAR_NUMBERS)

CURRENT_CONTEXT_SIGNAL=re.compile(
 r'\b(?:popravek|popravljam|mislil\w*|namesto|temveč|ampak|nadaljevanje|'
 r'nadaljuj\w*|zdaj|tokrat|current|continuing|correzione|intendo|invece|'
 r'ora|adesso|prosegu\w*)\b',re.I)
HISTORICAL_REPLACEMENT=re.compile(
 r'(?=.*\b(?:prej|predhodno|prejšnj\w*|prima)\b)(?=.*\b(?:namesto|invece)\b)',
 re.I|re.S)

_IT_ARTICLE_PREFIX=r"(?:(?:il|lo|la|un|una)\s+|l[’'])"
_IT_NAME_DESCRIPTOR_ATOM=(
 r'(?:sloven[oaie]|frances[ei]|italian[oaie]|maschil[ei]|femminil[ei]|'
 r'personal[ei]|propri[oaie]|stranier[oaie])')
_IT_NAME_DESCRIPTOR=(
 rf'(?:(?:di\s+persona)(?:\s+{_IT_NAME_DESCRIPTOR_ATOM}){{0,3}}'
 rf'|{_IT_NAME_DESCRIPTOR_ATOM}(?:\s+{_IT_NAME_DESCRIPTOR_ATOM}){{0,3}})')

def authoritative_grammar_slots(text,context):
 """Apply slots from context only when it explicitly marks a current update."""
 cases,numbers=explicit_grammar_slots(text)
 if (context and CURRENT_CONTEXT_SIGNAL.search(context)
     and not HISTORICAL_REPLACEMENT.search(context)):
  corrected_cases,corrected_numbers=explicit_grammar_slots(context)
  if corrected_cases:cases=corrected_cases
  if corrected_numbers:numbers=corrected_numbers
 return cases,numbers

def authoritative_grammar_sequence(structure,context):
 """Apply one explicit current correction atomically to ordered slot pairs."""
 if structure['ambiguity']:
  return [],structure['ambiguity']
 slots=copy.deepcopy(structure['slots'])
 if (not context or not CURRENT_CONTEXT_SIGNAL.search(context)
     or HISTORICAL_REPLACEMENT.search(context)):
  return slots,None
 cases,numbers=explicit_grammar_slots(context)
 if not cases and not numbers:return slots,None
 if len(cases)>1 or len(numbers)>1 or len(slots)>1:
  return [],'grammar_slot_pairing_unresolved'
 if not slots and not cases:
  return [],'grammar_slot_pairing_unresolved'
 previous=slots[0]if slots else{'case':None,'number':None}
 return [{'case':cases[0]if cases else previous['case'],
          'number':numbers[0]if numbers else previous['number']}],None

def corrected_context_targets(text,context):
 """Return every bounded current correction target; ambiguity stays explicit."""
 if not context or not CURRENT_CONTEXT_SIGNAL.search(context):return None
 # A statement about an earlier exchange is history, even when it quotes a
 # syntactically complete replacement. It must not supersede the live question.
 if HISTORICAL_REPLACEMENT.search(context):
  return None
 atom=r"[^\W\d_]+(?:[-’'][^\W\d_]+)*"
 offset=len(text)+1
 pairs={'«':'»','»':'«','“':'”','„':'“','"':'"',"'":"'"}
 def trim(start,end):
  while start<end and context[start].isspace():start+=1
  while end>start and context[end-1].isspace():end-=1
  return start,end
 def unquote(start,end):
  start,end=trim(start,end)
  if start<end and context[start]in pairs and context[end-1]==pairs[context[start]]:
   return trim(start+1,end-1)
  return start,end
 def target_object(start,end,allow_coordinated=False):
  """Return one exact lexical replacement plus its literal named context."""
  conjunctions={'ali','in','or','oppure','o','e'}
  start,end=trim(start,end);context_start,context_end=start,end
  quoted_start,quoted_end=unquote(start,end)
  if (quoted_start,quoted_end)!=(start,end):
   start,end=quoted_start,quoted_end;named_context=None
  else:
   prefix=re.match(
    rf'(?:(?:{_IT_ARTICLE_PREFIX})?'
    rf'(?:ime|imena|priimek|priimka|nome|cognome)\s+)',context[start:end],re.I)
   named_context=None
   if prefix:
    start+=prefix.end();start,end=trim(start,end)
    named_context=(context_start,context_end)
    quoted_start,quoted_end=unquote(start,end)
    if (quoted_start,quoted_end)!=(start,end):start,end=quoted_start,quoted_end
    else:
     descriptor=re.match(rf'(?:{_IT_NAME_DESCRIPTOR})\s+',context[start:end],re.I)
     if descriptor:start+=descriptor.end();start,end=trim(start,end)
     else:
      remainder=[match.group().casefold()for match in WORD.finditer(context[start:end])]
      if (len(remainder)!=1
          and not(allow_coordinated and set(remainder)&conjunctions)):return None
  value=context[start:end]
  words=[match.group().casefold()for match in WORD.finditer(value)]
  coordinated=[index for index,word in enumerate(words)if word in conjunctions]
  if (not value or len(words)>4
      or not re.fullmatch(rf'{atom}(?:\s+{atom}){{0,3}}',value,re.I)
      or (coordinated and (not allow_coordinated or len(coordinated)!=1
          or coordinated[0]in {0,len(words)-1}))):
   return None
  row={'text':value,'start':offset+start,'end':offset+end,
   'basis':('ambiguous correction replacement; original surface'if coordinated else
            'explicit_context_correction'),'span_scope':'context'}
  if named_context:
   left,right=named_context
   row['context_span']={'text':context[left:right],'start':offset+left,
    'end':offset+right,'basis':'correction named-object context; original surface',
    'span_scope':'context','role':'replacement'}
  return row
 frames=(
  r'\s*(?:popravek\s*:\s*)?namesto\s+(?P<old>[^;\n]{1,160}?)\s+'
  r'(?:uporabi|uporabite|uporabimo)\s+(?P<target>[^;\n]{1,160}?)\s*[.?!]*\s*',
  r'\s*(?:correzione\s*:\s*)?invece\s+(?:di|del|della)\s+'
  r'(?P<old>[^;\n]{1,160}?)\s+(?:usa|usate|usiamo|utilizza|utilizzate|utilizziamo)\s+'
  r'(?P<target>[^;\n]{1,160}?)\s*[.?!]*\s*')
 replacement=next((match for pattern in frames
  if(match:=re.fullmatch(pattern,context,re.I))),None)
 if replacement:
  old=target_object(*replacement.span('old'))
  target=target_object(*replacement.span('target'),allow_coordinated=True)
  if old is None or target is None:return []
  normalize=lambda value:' '.join(unicodedata.normalize('NFC',word.group()).casefold()
   for word in WORD.finditer(value))
  if normalize(old['text'])==normalize(target['text']):return None
  return [target]
 # A replacement cue that misses the complete frame is unresolved; quoted old
 # and new objects must never become two independent correction targets.
 if re.search(r'\b(?:namesto|invece)\b',context,re.I):return []
 candidates=[]
 for match in _TRANSLATION_QUOTES.finditer(context):
  start,end=match.span();candidates.append(target_object(start+1,end-1))
 if candidates:return []if any(row is None for row in candidates)else candidates
 label=re.search(rf'\b(?P<object>(?:(?:{_IT_ARTICLE_PREFIX})?'
  rf'(?:ime|imena|priimek|priimka|nome|cognome)\s+).+?)(?=\s*[.,;:!?]?\s*$)',context,re.I)
 if label:
  row=target_object(*label.span('object'))
  return [row]if row else[]
 return None

SCAFFOLD={'kaj','kako','kateri','katera','katere','katero','katerih','kdo','ali','je','so','sta','se','v','za','beseda','besede','besedo','besedi','prav','pravilno','prosim','katerega','pomeni','pomen','oblika','oblike','obliko','primer','napiše','reče','naj','mi','razloži','kakšen','kakšna','katere','danes','ni','brez','rečem','rečemo','rekel','iščem','stavek','stavka','kater','pravilen','poišči','navedi','pokaži','uporabi','preveri','to','ta','tisto','tem','tega','tale','več','povej','poveš','lahko','bolje','uporabiti','uporabljati'}
def lexical_word(text):
 t=text.strip().strip('"«»“”‘’?!.,;:');return bool(WORD.fullmatch(t))and not any(c.isdigit()for c in t)

_TRANSLATION_LANGUAGE=r'(?:v\s+(?:slovenščin\w*|italijanščin\w*)|in\s+(?:sloveno|italiano))'
_TRANSLATION_ACTION=r'(?:preved\w*|prevaj\w*|reče\w*)'
_TRANSLATION_FRAMES=(
 # The object's original span precedes or follows the translation action.
 rf'\s*(?:prosim[, ]+)?(?:kako\s+)?(?:se\s+)?(?:{_TRANSLATION_LANGUAGE}\s+)?{_TRANSLATION_ACTION}\s+(?P<target>.+?)\s*[.?!]*\s*',
 rf'\s*kako\s+(?:se\s+)?(?P<target>.+?)\s+{_TRANSLATION_ACTION}\s+{_TRANSLATION_LANGUAGE}\s*[.?!]*\s*',
 r'\s*(?:(?:kakšen|kateri|kater)\s+je\s+(?:pravilen\s+)?)?prevod\s+(?:za\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 r'\s*(?:kaj\s+je\s+)?(?:slovenska|italijanska)\s+ustreznica\s+za\s+(?P<target>.+?)\s*[.?!]*\s*',
 rf'\s*(?:come\s+si\s+)?(?:traduce|tradurre|traduci)\s+(?:{_TRANSLATION_LANGUAGE}\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 r"\s*(?:qual\s+[èe]\s+la\s+)?traduzione\s+di\s+(?P<target>.+?)\s*[.?!]*\s*",
)

_TASK_BOUNDARY=re.compile(
 r'(?:[?!.;]\s*|\s+(?:in|ter)\s+)(?=(?:ali|kaj|kako|zakaj)\b)'
 r'|\s+e\s+(?=in\s+che\s+cosa\b)',re.I)
_ACRONYM_SCOPE=re.compile(r'(?P<target>[^\W\d_]{2,})\s+(?:v|na|pri)\s+(?P<domain>[^,?;.!\n]{1,80})$',re.U)

def _scope_cut(text,start,end):
 """Separate explicit scope, without splitting ordinary prepositional terms."""
 candidate=text[start:end]
 domain=re.search(r'\s+v\s+[^,?;.]{1,50}\s+kontekstu\s*$',candidate,re.I)
 if domain:return start+domain.start()
 acronym=_ACRONYM_SCOPE.fullmatch(candidate)
 if acronym and acronym['target'].isupper():return start+acronym.end('target')
 return end

def request_object_spans(text):
 """Parse bounded task clauses independently; keep all original offsets.

 Coordinated requests are not pieces of a requested expression. Quoted spans
 are handled before this helper, and ordinary internal conjunctions stay whole.
 """
 starts=[0];ends=[]
 for boundary in _TASK_BOUNDARY.finditer(text):
  ends.append(boundary.start());starts.append(boundary.end())
 ends.append(len(text));result=[]
 for start,end in zip(starts,ends):
  clause=text[start:end]
  if is_referential_request(clause):continue
  for extractor,basis in ((translation_object_span,'unquoted translation object; whole original span'),
                          (lexical_object_span,'natural lexical object; whole original span')):
   span=extractor(clause)
   if span:
    a,b=span
    if clause[a:b].casefold()not in {'to','ta izraz','ta beseda'}:result.append((start+a,start+b,basis))
    break
 return result

def translation_object_span(text):
 """Keep an unquoted translation object whole; exact source lookup validates it.

 Only bounded, explicit question frames are interpreted here. Analyzer tokens
 cannot recover foreign function words reliably, and component hits must not
 silently replace the phrase requested by the reader.
 """
 for pattern in _TRANSLATION_FRAMES:
  match=re.fullmatch(pattern,text,re.I)
  if not match:continue
  start,end=match.span('target')
  object_text=text[start:end]
  label=re.match(r"(?:(?:besed(?:a|e|o|i)|izraz(?:a)?|termin(?:a)?|besedn[aoe]\s+zvez[aoe])\s+|(?:l['’])?(?:espressione|termine|parola)\s+)",object_text,re.I)
  if label:start+=label.end()
  language=re.search(rf'\s+{_TRANSLATION_LANGUAGE}\s*$',text[start:end],re.I)
  if language:end=start+language.start()
  end=_scope_cut(text,start,end)
  candidate=text[start:end].strip()
  if candidate and len(candidate)<=240 and len(list(WORD.finditer(candidate)))<=16 and not re.search(r'[?;\n]',candidate):
   return start,end
 return None

_TRANSLATION_QUOTES=re.compile(r'''«[^»\n]+»|»[^«\n]+«|“[^”\n]+”|„[^“”\n]+[“”]|"[^"\n]+"|(?<!\w)'[^'\n]+'(?!\w)''')

def translation_choice_spans(text):
 """Separate a translation source from two explicitly offered translations.

 A colon outside quotations is the source/choice boundary. Each side remains
 an exact whole expression, including function words; this creates no lexical
 equivalence or claim about which translation is correct.
 """
 span=translation_object_span(text)
 if not span:return []
 start,end=span
 quoted=[m.span()for m in _TRANSLATION_QUOTES.finditer(text,start,end)]
 def outside(a,b):return not any(left<b and a<right for left,right in quoted)
 colons=[m for m in re.finditer(':',text[start:end])if outside(start+m.start(),start+m.end())]
 if len(colons)!=1:return []
 colon=start+colons[0].start()
 boundaries=[m for m in re.finditer(r'\b(?:ali|oppure|or|o)\b',text[colon+1:end],re.I)
             if outside(colon+1+m.start(),colon+1+m.end())]
 if len(boundaries)!=1:return []
 split=boundaries[0];a=colon+1+split.start();b=colon+1+split.end()
 parts=[(start,colon),(colon+1,a),(b,end)];result=[]
 pairs={'«':'»','»':'«','“':'”','„':'“','"':'"',"'":"'"}
 for i,(left,right)in enumerate(parts):
  while left<right and text[left].isspace():left+=1
  while right>left and text[right-1].isspace():right-=1
  if i==0:
   before=re.match(rf'{_TRANSLATION_LANGUAGE}\s+',text[left:right],re.I)
   if before:left+=before.end()
   after=re.search(rf'\s+{_TRANSLATION_LANGUAGE}$',text[left:right],re.I)
   if after:right=left+after.start()
  if left<right and text[left]in pairs and text[right-1]==pairs[text[left]]:left+=1;right-=1
  candidate=text[left:right]
  if (not candidate or len(candidate)>240 or len(list(WORD.finditer(candidate)))>16
      or re.search(r'[?;\n:«»“”„"]',candidate)):
   return []
  result.append({'start':left,'end':right,'text':candidate,
   'basis':'translation source expression; original surface'if i==0 else'proposed translation alternative; original surface'})
 return result if result[1]['text']!=result[2]['text']else []

SL_CASE_WORD=r'(?:imenovalnik|rodilnik|dajalnik|tožilnik|mestnik|orodnik)'
IT_CASE_WORD=r'(?:nominativo|genitivo|dativo|accusativo|locativo|strumentale)'
SL_NUMBER_WORD=r'(?:ednine|dvojine|množine)'
IT_NUMBER_WORD=r'(?:singolare|plurale)'
_LEXICAL_FRAMES=(
 rf'\s*(?:kako\s+se\s+glasi\s+)?{SL_CASE_WORD}(?:\s+(?:ali|in)\s+{SL_CASE_WORD})+\s+(?:(?:imena|priimka|besede|samostalnika|izraza)\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 rf'\s*(?:(?:qual\s+[èe]\s+|quali\s+sono\s+)(?:il\s+)?)?{IT_CASE_WORD}(?:\s+(?:oppure|o|e)\s+(?:il\s+)?{IT_CASE_WORD})+\s+(?:(?:del\s+)?(?:nome|cognome|parola|sostantivo|termine)\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 rf'\s*(?:kako\s+se\s+glasi\s+)?{SL_CASE_WORD}\s+{SL_NUMBER_WORD}(?:\s+(?:ali|in)\s+{SL_NUMBER_WORD})+\s+(?:(?:imena|priimka|besede|samostalnika|izraza)\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 rf'\s*(?:(?:qual\s+[èe]\s+|quali\s+sono\s+)(?:il\s+)?)?{IT_CASE_WORD}\s+{IT_NUMBER_WORD}(?:\s+(?:oppure|o|e)\s+{IT_NUMBER_WORD})+\s+(?:(?:del\s+)?(?:nome|cognome|parola|sostantivo|termine)\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 rf'\s*kako\s+(?:(?:naj|lahko)\s+)?(?:se\s+)?(?:{SL_INFLECTION_ACTION})\s+(?P<target>.+?)\s*[.?!]*\s*',
 rf'\s*come\s+si\s+(?:{IT_INFLECTION_ACTION})\s+(?:(?:il\s+)?(?:nome|cognome|parola|sostantivo|termine)\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 r'\s*(?:in\s+che\s+cosa\s+)?differisce\s+(?:dal|dalla)\s+(?:nome|cognome|parola|sostantivo|termine)\s+(?P<target>.+?)\s*[.?!]*\s*',
 r'\s*(?:kaj\s+(?:je(?!\s+(?:prav|pravil\w*|ustrez\w*)\b)|pomeni)|pomen\s+besede)\s+(?P<target>.+?)\s*[.?!]*\s*',
 r'\s*(?:(?:poišči|iščem|prosim|navedi|katere\s+so)\s+)?(?:sopomenk\w*|sinonim\w*)\s+(?:za\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 r'\s*(?:v\s+katerem\s+sklonu\s+je|kateri\s+sklon\s+je)\s+(?P<target>.+?)\s*[.?!]*\s*',
 r'\s*(?:rodilnik|dajalnik|tožilnik|orodnik|mestnik)\s+(?:ednine|dvojine|množine)\s+(?:besede\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 r'\s*(?:(?:qual\s+[èe]\s+il|come\s+si\s+presenta\s+il)\s+)?(?:nominativo|genitivo|dativo|accusativo|locativo|strumentale)\s+(?:(?:singolare|plurale)\s+)?(?:(?:del\s+)?(?:nome|cognome|parola|sostantivo|termine)\s+)?(?P<target>.+?)\s*[.?!]*\s*',
 r'\s*(?:ali|a)\s+se\s+(?P<target>.+?)\s+(?:uporablja|rabi|velja)\b[^?!.]*[?!.]?\s*',
 r'\s*(?:ali\s+)?je\s+(?P<target>.+?)\s+(?:priporočen[ai]?|odsvetovan[ai]?|nezaželen[ai]?|pravilen)\s+(?:izraz|termin|beseda)\s*[?!.]*\s*',
)

def lexical_object_span(text):
 """Bounded natural wrappers keep the entire expression, including function words."""
 for pattern in _LEXICAL_FRAMES:
  match=re.fullmatch(pattern,text,re.I)
  if not match:continue
  start,end=match.span('target')
  label=re.match(r'(?:besed[aeoiu]|izraz(?:a|u)?|termin(?:a)?|zveza|zvezo|(?:osebno\s+)?im(?:e|ena)|samostalnik)\s+',text[start:end],re.I)
  if label:start+=label.end()
  language_scope=re.search(
   r'\s+v\s+(?:slovenščin\w*|italijanščin\w*)\s*$',text[start:end],re.I)
  if language_scope:end=start+language_scope.start()
  end=_scope_cut(text,start,end)
  if start<end and len(list(WORD.finditer(text[start:end])))<=16:return start,end
 return None

def named_lexical_target(span,original=None):
 """Separate an explicit name label from an unquoted inflection object.

 Descriptors remain literal context, never a grammar verdict or a replacement
 for the spelling supplied by the user. Quoted expressions and unrestricted
 noun phrases do not enter this helper.
 """
 text=span['text']
 match=re.fullmatch(r'(?P<descriptors>(?:[^\W\d_]+\s+){1,4})(?:ime|priimek)\s+(?P<name>.+)',text,re.U|re.I)
 context=copy.deepcopy(span)
 if match:
  descriptors=match['descriptors'].split()
  if not all(_NAME_DESCRIPTOR.fullmatch(word)for word in descriptors):return span
 elif original is not None:
  # Italian places the named-object role before its descriptors (``nome
  # sloveno Hugo``). The lexical frame has already consumed that role, so
  # recover only the immediately adjacent literal label and retain the whole
  # named phrase as context while nominating the exact supplied name.
  role=re.search(r'\b(?:nome|cognome)\s+$',original[:span['start']],re.I)
  match=re.fullmatch(
   rf'(?P<descriptors>(?:{_IT_NAME_DESCRIPTOR}\s+){{1,4}})(?P<name>.+)',text,re.I)
  if not role or not match:return span
  context.update(text=original[role.start():span['end']],start=role.start(),end=span['end'])
 else:return span
 start,end=match.span('name');name=text[start:end]
 if not 1<=len(list(WORD.finditer(name)))<=8 or re.search(r'[«»“”"?!;\n]',name):return span
 return {'text':name,'start':span['start']+start,'end':span['start']+end,
  'basis':'named lexical object; descriptor context retained','context_span':context}

def is_referential_request(text):
 """Only an explicit demonstrative task can bind its supplied context."""
 return bool(re.fullmatch(r'\s*(?:(?:a|ali)\s+)?(?:se\s+)?(?:to|ta izraz|ta beseda)\s+(?:tudi\s+)?(?:v\s+\w+\s+)?(?:uporablja|rabi|velja)(?:\s+.+)?[?.!]?\s*',text,re.I)
  or re.fullmatch(r'\s*(?:kako|kaj)\s+(?:se\s+)?(?:to|ta izraz|ta beseda)\s+(?:pomeni|pove|reče|prevede|sklanja|napiše|zapiše)(?:\s+[^?!.]+)?[?.!]?\s*',text,re.I)
  or re.fullmatch(r'\s*(?:kako\s+se\s+(?:prevede|sklanja|napiše|zapiše)|(?:ali\s+)?(?:to\s+)?obstaja|(?:ali\s+)?se\s+(?:to\s+)?uporablja)(?:\s+(?:tudi\s+)?(?:v|na|po)\s+[^?!.]+)?[?.!]?\s*',text,re.I)
  or re.fullmatch(r'\s*(?:come\s+si\s+(?:traduce|declina|scrive)|(?:questo\s+)?esiste|si\s+usa)(?:\s+(?:anche\s+)?in\s+[^?!.]+)?[?.!]?\s*',text,re.I))

def bound_context_target(text,context,language='und'):
 """Resolve one explicitly supplied expression/question; never search user history."""
 if not context.strip() or not is_referential_request(text):return None
 interpretation=plan(context,'',language,{'raw_engines':{}})
 targets=interpretation['targets']
 allowed={'single lexical surface','bounded whole phrase before component lookup','quoted original span',
  'natural lexical object; whole original span','unquoted translation object; whole original span','requested lexical object',
  'named lexical object; descriptor context retained'}
 if len(targets)!=1 or targets[0]['basis']not in allowed:return None
 target=copy.deepcopy(targets[0]);offset=len(text)+1
 target.update(start=target['start']+offset,end=target['end']+offset,span_scope='context',
  context_target_basis=target['basis'],basis='explicit_context_target')
 if target['context_target_basis']=='named lexical object; descriptor context retained':
  descriptor=target['context_span'];descriptor.update(start=descriptor['start']+offset,
   end=descriptor['end']+offset,span_scope='context')
 return target

def portable_request_plan(interpretation,language='und'):
 """Portable request data: no ontology assignment or guessed spelling correction."""
 text=interpretation['original'];context=interpretation.get('context','');combined=text+'\n'+context
 destination=[code for code,pattern in [('sl',r'v\s+slovenščin[io]|po\s+slovensko|(?:pove|povemo|reče|rečemo)\s+slovensko|slovenska\s+ustreznica|in\s+sloveno'),('it',r'v\s+italijanščin[io]|po\s+italijansko|(?:pove|povemo|reče|rečemo)\s+italijansko|italijanska\s+ustreznica|in\s+italiano')]if re.search(pattern,combined,re.I)]
 facets=interpretation['requested_facets'];operation=('translation_help'if'translation'in facets else'explanation'if'meaning'in facets else'rule_lookup'if any(x in facets for x in ('morphology','orthography'))else None)
 # The internal route remains the broad morphology capability, while the
 # portable signed plan must preserve an explicitly parsed case slot. A broad
 # request is serialized as ``inflection`` without inventing a narrower slot.
 grammar_slots=copy.deepcopy(interpretation.get('requested_grammar_slots',[]))
 if grammar_slots:
  cases=list(dict.fromkeys(row['case']for row in grammar_slots))
  numbers=list(dict.fromkeys(row['number']for row in grammar_slots
                             if row['number']is not None))
 else:cases,numbers=authoritative_grammar_slots(text,context)
 broad_inflection=bool(re.search(rf'\b(?:{INFLECTION_CUE})\b',text,re.I))
 grammar_ambiguities=interpretation.get('grammar_ambiguities',[])
 if grammar_ambiguities:
  case_order={name:index for index,(name,_)in enumerate(GRAMMAR_CASES)}
  number_order={name:index for index,(name,_)in enumerate(GRAMMAR_NUMBERS)}
  cases=sorted(set(cases),key=case_order.__getitem__)
  numbers=sorted(set(numbers),key=number_order.__getitem__)
 if cases:
  portable_morphology=cases+(
   ['inflection']if broad_inflection and grammar_ambiguities else[])
 else:
  portable_morphology=['inflection']if broad_inflection else['morphology']
 portable_facets=[]
 for facet in facets:
  portable_facets.extend(portable_morphology if facet=='morphology'else[facet])
 portable_facets.extend(numbers)
 domain=re.search(r'\bv\s+([^,?;.]{1,50})\s+kontekstu\b',combined,re.I)
 domain_text=domain.group(1)if domain else None
 if domain_text is None:
  for target in interpretation['targets']:
   if target['text'].isupper() and len(target['text'])>1:
    suffix=combined[target['start']:]
    boundary=_TASK_BOUNDARY.search(suffix)
    suffix=suffix[:boundary.start()]if boundary else suffix
    scoped=_ACRONYM_SCOPE.fullmatch(suffix.strip().rstrip('?.!'))
    if scoped:domain_text=scoped['domain'];break
 proposal=bool(re.search(r'predlog\w*\s+pravil|pravopis\s*8|predlagan\w*\s+pravopis',combined,re.I))
 geography=[{'raw':m.group(),'start':m.start(),'end':m.end(),'basis':'explicit_location_wording'}for m in re.finditer(r'\b(?:v|po)\s+(?:Italij[ai]|Slovenij[ai]|Trstu|Goric[ai]|FJK|FVG)\b',combined,re.I)]
 targets=[{key:copy.deepcopy(target[key])for key in ('text','start','end','basis','context_span','span_scope','context_target_basis')if key in target}for target in interpretation['targets']]
 return {'version':REQUEST_VERSION,'original':text,'context':context,'target_spans':targets,
  'candidate_lemmas':[{'target':t['text'],'candidates':list(t.get('lemma_candidates',[])),'basis':'analyzer_nomination_requires_resource_match'}for t in interpretation['targets'] if t.get('lemma_candidates')],
  'operation':operation,'source_language':language if language in {'sl','it'}else None,
  'translation_language':destination[0]if len(destination)==1 else None,'response_language':None,
  'requested_facets':list(dict.fromkeys([*portable_facets,*[name for name,pattern in [('example',r'primer|stavk'),('explanation',r'zakaj|razlag'),('spacing',r'skupaj|narazen'),('recommendation',r'priporočen|odsvetovan|nezaželen'),('usage',r'uporablja|rabi\b')]if re.search(pattern,text,re.I)]])),'requested_grammar_slots':grammar_slots,'domain':domain_text,'geography':geography,'jurisdiction':None,
  'requested_source':'pravopis8'if proposal else None,'requested_edition':'proposal'if proposal else None,
  'ambiguities':(['translation_direction_unspecified']if operation=='translation_help'and len(destination)!=1 else[])+(['unresolved_reference']if not targets and is_referential_request(text)else[])+(['coordinated_target_unresolved']if any(target.get('basis')=='ambiguous correction replacement; original surface'for target in targets)else[])+list(interpretation.get('grammar_ambiguities',[])),
  'confidence':'bounded_surface'if targets else'broad_routes'}

_COMPARISON_CONTEXT={'brez','ni','ne','je','so','sta','smo','ste','sem','si','se','v','za','na','pri','od','do','z','s','iz','ob','po','k','h'}

def comparison_lexical_spans(text,spans):
 """Separate contrasted lexical forms from shared grammatical clause context.

 Shared content words stay part of the lexical object (e.g. place names).
 Whole clauses and offsets remain attached, so removing a shared negation or
 preposition from the lookup never discards the question's grammatical context.
 """
 if len(spans)!=2 or not re.fullmatch(r'''\s*(?:[«»“”"'‘’]\s*)?(?:ali|or|oppure)(?:\s*[«»“”"'‘’])?\s*''',text[spans[0]['end']:spans[1]['start']],re.I):return spans
 tokens=[list(WORD.finditer(span['text']))for span in spans]
 if not all(tokens):return spans
 left=0;right=0;minimum=min(map(len,tokens))
 while left<minimum-1:
  shared={row[left].group().casefold()for row in tokens}
  if len(shared)!=1 or not shared<=_COMPARISON_CONTEXT:break
  left+=1
 while right<minimum-left-1:
  shared={row[-right-1].group().casefold()for row in tokens}
  if len(shared)!=1 or not shared<=_COMPARISON_CONTEXT:break
  right+=1
 if not(left or right):return spans
 result=[]
 for span,row in zip(spans,tokens):
  first,last=row[left],row[len(row)-right-1]
  result.append({'start':span['start']+first.start(),'end':span['start']+last.end(),
      'text':span['text'][first.start():last.end()],
      'basis':'contrasted original form within shared grammatical context',
      'context_span':dict(span)})
 return result if len({span['text']for span in result})>1 else spans

def negated_existence_frame(text,spans):
 """A bare 'X ni'/'ni X' contrast requests genitive; predicates do not."""
 clauses=[span.get('context_span',span)['text']for span in spans]
 def bare(clause):
  words=[m.group().casefold()for m in WORD.finditer(clause)]
  return len(words)==2 and words.count('ni')==1 and all(w=='ni'or w not in SCAFFOLD for w in words)
 if clauses and all(bare(clause)for clause in clauses):return True
 # Ordinary unquoted question scaffolding may surround the same bare frame.
 stripped=re.sub(r'^\s*(?:je\s+prav\s+|ali\s+|danes\s+)', '',text,flags=re.I)
 return bare(stripped)

def plan(text,context,language,analysis,request_plan=None):
 words=list(WORD.finditer(text));requested=[k for k,p in FACETS.items()if re.search(p,text,re.I)]
 if ('morphology'in requested and'translation'in requested
     and not re.search(r'prevod|preved|prevaj|ustreznic|traduc|traduz|'
                       r'(?:pove|povemo|reče|rečemo)\s+(?:po\s+)?'
                       r'(?:slovensko|italijansko)',text,re.I)):
  requested.remove('translation')
 grammar=structured_grammar_request(text)
 hints=[w for r in analysis.get('raw_engines',{}).values()for w in r.get('analysis',{}).get('words',[])];spans=[];kind='question';basis=[]
 def add(a,b,why):
  if any(m.start()<b and a<m.end() for m in re.finditer(r'https?://\S+|\b\w*[0-9_]\w*\b',text)):return
  while a<b and text[a].isspace():a+=1
  while b>a and text[b-1]in' \n\t?.!,;:':b-=1
  if a<b and not any(s['start']==a and s['end']==b for s in spans):spans.append({'start':a,'end':b,'text':text[a:b],'basis':why})
 if request_plan is not None:
  if request_plan.get('version')!=REQUEST_VERSION or request_plan.get('original')!=text or request_plan.get('context','')!=context:raise ValueError('Request plan input differs')
  for target in request_plan.get('target_spans',[]):
   a,b=target.get('start'),target.get('end')
   source=text+'\n'+context if target.get('span_scope')=='context'else text
   if type(a)is not int or type(b)is not int or not 0<=a<b<=len(source) or source[a:b]!=target.get('text') or (target.get('span_scope')=='context'and a<=len(text)):raise ValueError('Request target span differs')
   spans.append(copy.deepcopy(target))
 elif grammar['matched']:
  if grammar['target']:
   add(*grammar['target'],'natural lexical object; whole original span')
   spans=[named_lexical_target(span,text)for span in spans]
 elif is_referential_request(text):
  target=bound_context_target(text,context,language)
  if target:spans.append(target)
 if spans:
  kind='comparison'if len(spans)>1 else'word'if lexical_word(text)else'phrase'if len(list(WORD.finditer(spans[0]['text'])))>1 else'question'
 elif grammar['matched']:pass
 elif is_referential_request(text):pass
 elif lexical_word(text):
  m=WORD.search(text);add(m.start(),m.end(),'single lexical surface');kind='word'
 elif len(words)<=8 and not requested and words and (words[0].group().casefold()not in SCAFFOLD or words[0].group().casefold()in {'v','na','pri','ob','po','k','h','z','s','od','do','iz'}) and '?'not in text and not re.search(r'[«»“”"]|\bali\b',text,re.I):
  add(0,len(text),'bounded whole phrase before component lookup');kind='phrase'
 else:
  if 'translation'in requested:spans=translation_choice_spans(text)
  if spans:kind='comparison'
  else:
   for m in re.finditer(r'«([^»]+)»|»([^«]+)«|“([^”]+)”|"([^"]+)"',text):
    gi=next(i for i,v in enumerate(m.groups(),1)if v is not None);add(*m.span(gi),'quoted original span')
  if len(spans)>1 and 'translation'not in requested:
   contrasted=comparison_lexical_spans(text,spans)
   if contrasted!=spans:spans=contrasted;kind='comparison'
  if not spans:
   for a,b,why in request_object_spans(text):add(a,b,why)
   if 'morphology'in requested:
    spans=[named_lexical_target(span,text)if span['basis']=='natural lexical object; whole original span'else span for span in spans]
   if len(spans)>1:kind='comparison'
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
   cue=re.search(r'\b(?:besed[aeoiu]|izraza?|im(?:e|ena)|termina?)\s+(.+)',text,re.I)
   if cue:add(cue.start(1),cue.end(1),'requested lexical object')
  if not spans:
   # Statistical structure nominates content spans; lookup verifies them later.
   candidates=[w for w in hints if w.get('upos')in {'NOUN','PROPN','ADJ','VERB'}and w.get('start')is not None and w['text'].casefold()not in SCAFFOLD and not any(re.search(p,w['text'],re.I)for p in FACETS.values())]
   for w in candidates[-4:]:add(w['start'],w['end'],'analyzer content span')
  if not spans and len(words)<=8 and words:
   for w in words:
    if w.group().casefold()not in SCAFFOLD and not any(re.search(p,w.group(),re.I)for p in FACETS.values()):add(w.start(),w.end(),'degraded lexical candidate; source match required')
 corrected=corrected_context_targets(text,context)
 if corrected is not None:
  spans=corrected;kind='comparison'if len(corrected)>1 else'question'
 if len(spans)>1 and len({s['text'].casefold()for s in spans})==1 and len({s['text']for s in spans})>1 and 'orthography'not in requested:requested.append('orthography')
 if kind=='question'and len(spans)==1 and len(list(WORD.finditer(spans[0]['text'])))>1:kind='phrase'
 langs=[language]if language in {'sl','it'}else ['sl','it']
 # Direction cues describe target policy, not certain input-language detection.
 if language=='und'and re.search(r'\b(?:besed|sopomen|sklanj|rodilnik|kako|kaj|kdo|kateri|ali|brez|danes ni)',text,re.I):langs=['sl']
 for s in spans:
  s['lemma_candidates']=list(dict.fromkeys(w['lemma']for w in hints if w.get('start')is not None and s['start']<=w['start']and w['end']<=s['end']and w['lemma']))[:6]
  s['lemma_sequences']=list(dict.fromkeys(' '.join(w['lemma']for w in r.get('analysis',{}).get('words',[])if w.get('start')is not None and s['start']<=w['start']and w['end']<=s['end'])for r in analysis.get('raw_engines',{}).values()))
  s['context_features']=[{'engine':name,'feats':w['feats'],'upos':w['upos']}for name,r in analysis.get('raw_engines',{}).items()for w in r.get('analysis',{}).get('words',[])if w.get('start')is not None and s['start']<=w['start']and w['end']<=s['end']]
 grammar_slots,grammar_ambiguity=authoritative_grammar_sequence(grammar,context)
 cases,numbers=authoritative_grammar_slots(text,context)
 case=grammar_slots[0]['case']if len(grammar_slots)==1 else None
 if not grammar_slots and not cases:
  case='genitive'if re.search(r'\bbrez\b',text,re.I)or negated_existence_frame(text,spans)else'nominative'if re.search(r'\bkdo je\b',text,re.I)else None
 number=(grammar_slots[0]['number']if len(grammar_slots)==1 else
         numbers[0]if len(numbers)==1 else'singular'if case and not cases else None)
 result={'version':VERSION,'query_kind':kind,'original':text,'context':context,'targets':spans[:4],'targets_truncated':len(spans)>4,'requested_facets':requested,'requested_grammar_slots':grammar_slots,'grammar_ambiguities':[grammar_ambiguity]if grammar_ambiguity else[],'inferred_facets':['loris','morphology','frequency','synonyms','collocations','terminology'],'lookup_languages':langs,'language_basis':'explicit'if language!='und'else'bounded inventory/wording policy, not certain language detection','slot_focus':{'case':case,'number':number,'basis':'grammatical question/context cue; verify against source slot'}if case else None,'interpretation_uncertainty':'multiple resource identities and senses remain separate; no automatic normative verdict','basis':['original surface','question structure','raw analyzer content spans','resource validation follows']}

 result['request_plan']=portable_request_plan(result,language)
 return result
