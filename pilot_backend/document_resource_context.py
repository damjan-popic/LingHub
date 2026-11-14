"""Occurrence-bound retained ordinary units, never executable edit contracts."""
from copy import deepcopy
import hashlib,json,re,unicodedata

VERSION='klikai.document-ordinary-context.v1'
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def reference_nomination(passage,occurrence_text):
    """Mechanical topic nomination only; it never resolves applicability."""
    norm=lambda v:' '.join(unicodedata.normalize('NFKC',v).casefold().split())
    topic='\n'.join(v for v in (passage.get('title'),(passage.get('context')or{}).get('question'))if isinstance(v,str))
    quoted=re.findall(r'»([^»«\n]+)«|«([^«»\n]+)»|“([^“”\n]+)”|"([^"\n]+)"',topic)
    target=norm(occurrence_text)
    for group in quoted:
        form=next(v for v in group if v)
        if 1<=len(form)<=200 and re.search(r'(?<!\w)'+re.escape(norm(form))+r'(?!\w)',target):return 'literal_source_quoted_topic'
    title=passage.get('title')or''
    if passage.get('role')=='dictionary_entry'and 1<=len(title.split())<=6 and re.search(r'(?<!\w)'+re.escape(norm(title))+r'(?!\w)',target):return 'literal_source_entry_title'
    stop={'kako','zakaj','kateri','katera','katere','katero','pravilno','pravilna','beseda','besede','besedo','pisanje','raba','rabe','lahko','vprašanje','odgovor','slovenskem','slovensko','slovenščini','tudi','glede'}
    words=lambda v:{w for w in re.findall(r'[^\W\d_]{3,}',norm(v))if w not in stop}
    return 'source_question_title_topic_overlap'if len(words(topic)&words(occurrence_text))>=2 else None

def records(value,text,source_sha256):
    if value is None:return []
    if (value.get('version')!=VERSION or value.get('source_sha256')!=source_sha256
            or value.get('context_sha256')!=digest({k:v for k,v in value.items()if k!='context_sha256'})
            or not isinstance(value.get('envelopes'),list)or len(value['envelopes'])>64):raise ValueError('Ordinary context binding differs')
    found={}
    for envelope in value['envelopes']:
        occurrence=envelope['occurrence'];a,b=occurrence['start'],occurrence['end']
        if (type(a)is not int or type(b)is not int or not 0<=a<b<=len(text)
                or text[a:b]!=occurrence['text']or occurrence['original_sha256']!=source_sha256):raise ValueError('Resource occurrence differs')
        tool=envelope.get('tool_evidence')
        if tool:
            query=tool.get('captures',[{}])[0].get('response',{}).get('query',{}).get('text')
            if query!=occurrence['text']or envelope.get('query')!=occurrence['text']:raise ValueError('Lexical query occurrence differs')
        reference=envelope.get('reference_response')
        if reference and(reference.get('question')!=occurrence['text']or envelope.get('query')!=occurrence['text']):raise ValueError('Reference query occurrence differs')
        for bound in envelope.get('all_occurrences')or[occurrence]:
            if (type(bound.get('start'))is not int or type(bound.get('end'))is not int or not 0<=bound['start']<bound['end']<=len(text)or text[bound['start']:bound['end']]!=bound.get('text')or bound.get('original_sha256')!=source_sha256):raise ValueError('Repeated resource occurrence differs')
        for passage in envelope.get('source_passages',[])+envelope.get('tool_passages',[]):
            eid=passage.get('passage_id');body=passage.get('text')
            if (not isinstance(eid,str)or not eid or not isinstance(body,str)or not body
                    or len(body)>65536 or not isinstance(passage.get('source_slug'),str)
                    or not isinstance(passage.get('source_revision'),str)):raise ValueError('Native context passage differs')
            raw={k:deepcopy(passage[k])for k in ('title','url','source_revision','source_status','edition','date','publication_scope')if k in passage}
            raw.update(original_text=body,source_unit=deepcopy(passage),language=envelope['language'],
                coverage_note=passage.get('tool_scope')or'Qualified retained source context; relevance is not applicability or correction authority.')
            row={'evidence_id':eid,'capture_kind':'ordinary_context_unit','capture':raw,'capture_sha256':digest(raw),
                'source_slug':passage['source_slug'],'external_id':passage.get('source_id')or eid,
                'rights_mode':passage.get('rights_mode')if isinstance(passage.get('rights_mode'),str)else'restricted',
                'context_eligible':passage.get('context_eligible',True)and not passage.get('incomplete',False),
                'source_role':passage.get('role'),'tool_kind':passage.get('tool_kind'),
                'occurrences':deepcopy(envelope.get('all_occurrences')or[occurrence]),'native_passage':deepcopy(passage),
                'model_context_eligible':passage.get('context_eligible',True)or passage.get('role')=='tool_result'and passage.get('tool_kind')in {'synonyms','collocations','morphology'},
                'context_use':'candidate_context_only_not_sentence_applicability'}
            nomination=('ordinary_source_owned_lexical_candidate'if passage.get('role')=='tool_result'else reference_nomination(passage,occurrence['text']))
            row['document_admission']={'role':'candidate_context'if nomination else'retained_lookup_only',
                'basis':nomination or'no_supported_source_topic_nomination','applicability_not_established':True}
            question=(passage.get('context')or{}).get('question')
            reader_text=(('Izvirno vprašanje:\n'+question+'\n\n')if isinstance(question,str)else'')+body
            row['source']={'source_id':passage.get('source_id')or eid,'source_revision':passage['source_revision'],
                'evidence_id':eid,'title':passage.get('title')or row['external_id'],
                'source_label':passage.get('source_label')or passage['source_slug'],'url':passage.get('url'),
                'excerpt':reader_text,'reader_blocks':[{'heading':passage.get('source_label')or passage['source_slug'],'text':reader_text}],
                'locator':deepcopy(passage.get('span')or{}),
                'bibliography':{k:deepcopy(passage[k])for k in ('source_id','source_revision','authors','date','edition','source_status','publication_scope','tool_scope','editorial_status','tool_provenance','context')if k in passage},
                'native_source_unit':deepcopy(passage)}
            if eid in found:
                if found[eid]['capture_sha256']!=row['capture_sha256']:raise ValueError('Conflicting native source identity')
                for bound in row['occurrences']:
                    if bound not in found[eid]['occurrences']:found[eid]['occurrences'].append(deepcopy(bound))
            else:found[eid]=row
    if len(found)>512:raise ValueError('Document context unit bound')
    return list(found.values())

def finding_notice(record,observed):
    p=record['native_passage'];kind=p.get('tool_kind');unit=p.get('original_json')or{};target=unit.get('target')or observed['text']
    if kind=='collocations':
        fields=unit.get('fields')or{};surface=fields.get('surface')
        if surface:
            note='Vir beleži zvezo »'+surface+'«.'
            if fields.get('frequency')is not None:note+=' Pogostnost: '+str(fields['frequency'])+'.'
            if fields.get('logDice')is not None:note+=' Povezanost logDice: '+str(fields['logDice'])+'.'
            return note+' Pogostnost in povezanost sta različni meri; redkost ali odsotnost ne dokazuje napake.'
    if kind=='synonyms':
        relations=(unit.get('fields',{}).get('sense')or{}).get('relations',[])
        forms=[r['raw_text']for r in relations if r.get('relation_type')=='synonym'and isinstance(r.get('raw_text'),str)]
        wording=(' Med zapisanimi odnosi: '+', '.join(forms)+'.')if forms else''
        return 'Vir navaja sopomenske odnose za »'+target+'«.'+wording+' Ohranite zapisani pomen, smer odnosa, register in področje; pomen v tej povedi ni potrjen in besedilo ni samodejno zamenjano.'
    if kind=='morphology':return 'Vir navaja zapisane oblike in možne slovnične razlage za »'+target+'«. Upoštevajte vse različice; pomen, sklon in število v tej povedi niso samodejno potrjeni.'
    return 'Vir vsebuje pojasnilo, povezano z odsekom. Upoštevajte celotno vprašanje, odgovor, pogoje in status vira; uporabnost v tej povedi zahteva presojo.'
