"""Occurrence-bound retained ordinary units, never executable edit contracts."""
from copy import deepcopy
import hashlib,json,re,unicodedata

VERSION='klikai.document-ordinary-context.v1'
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def native_exclusion(passage):
    if passage.get('rights_mode')in {'linked_only','metadata_only','unknown'}:return 'native_rights_do_not_admit_content'
    if (passage.get('incomplete')or passage.get('missing_content')or passage.get('missing_fields')
            or passage.get('content_status')in {'incomplete','unavailable','metadata_only','linked_only'}
            or 'incomplete_answer_group'in passage.get('warnings',[])):return 'native_content_incomplete_or_missing'
    return None

def same_native_projection(expected,retained):
    """Field maps are mappings; retain their native serialization unchanged."""
    def normalized(units):
        units=deepcopy(units)
        for unit in units:
            projection=unit.get('writer_projection')
            if isinstance(projection,dict)and isinstance(projection.get('field_map'),list):
                projection['field_map']=sorted(projection['field_map'],key=lambda v:json.dumps(v,ensure_ascii=False,sort_keys=True))
        return units
    return normalized(expected)==normalized(retained)

def native_question(passage):
    context=passage.get('context')
    return context.get('question')if isinstance(context,dict)else None

def reference_nomination(passage,occurrence_text):
    """Mechanical topic nomination only; it never resolves applicability."""
    norm=lambda v:' '.join(unicodedata.normalize('NFKC',v).casefold().split())
    topic='\n'.join(v for v in (passage.get('title'),native_question(passage))if isinstance(v,str))
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

def frozen_reader_group(record,context):
    """Display one complete captured answer, keeping its requested chunk intact.

    Reuse the per-response sibling bindings established by ``records``. Never
    fill a gap with a sibling from another lookup or infer a missing ending.
    """
    requested=record['native_passage']
    if requested.get('role')!='expert_answer'or record['source']['bibliography'].get('context_exclusion'):return None
    for binding in record['native_bindings']:
        if not binding['reader_complete_group']or binding['native_exclusion']:continue
        envelope=context['envelopes'][binding['envelope_index']]
        reference=envelope.get('reference_response')or{}
        if digest(reference)!=binding['reference_response_sha256']:continue
        available={p['passage_id']:p for p in envelope.get('source_passages',[])}
        siblings=[]
        for member in binding['native_siblings']:
            passage=available.get(member['passage_id'])
            if passage is None or digest(passage)!=member['passage_sha256']:break
            siblings.append(passage)
        else:
            siblings.sort(key=lambda p:(p.get('span')or{}).get('char_start',-1))
            body='';valid=bool(siblings)
            for passage in siblings:
                span=passage.get('span')or{};a,b=span.get('char_start'),span.get('char_end');text=passage['text']
                if (type(a)is not int or type(b)is not int or not 0<=a<b
                        or b-a!=len(text)or a>len(body)
                        or body[a:min(b,len(body))]!=text[:max(0,min(b,len(body))-a)]):
                    valid=False;break
                body+=text[max(0,len(body)-a):]
            if not valid:continue
            return {'requested_passage_id':record['evidence_id'],
                'requested_passage_sha256':digest(requested),
                'requested_span':deepcopy(requested['span']),
                'requested_text_sha256':hashlib.sha256(requested['text'].encode()).hexdigest(),
                'source_group':deepcopy(binding['source_group']),'post_id':binding['post_id'],
                'reference_response_sha256':binding['reference_response_sha256'],
                'members':deepcopy(binding['native_siblings']),
                'original_text':body,'body_sha256':hashlib.sha256(body.encode()).hexdigest(),
                'coordinate_base':'assembled_frozen_group_unicode_codepoint_half_open',
                'question':native_question(siblings[0]),'scope':'one_complete_frozen_reader_response'}
    return None

def records(value,text,source_sha256):
    if value is None:return []
    if (value.get('version')!=VERSION or value.get('source_sha256')!=source_sha256
            or value.get('context_sha256')!=digest({k:v for k,v in value.items()if k!='context_sha256'})
            or not isinstance(value.get('envelopes'),list)or len(value['envelopes'])>64):raise ValueError('Ordinary context binding differs')
    found={}
    for envelope_index,envelope in enumerate(value['envelopes']):
        occurrence=envelope['occurrence'];a,b=occurrence['start'],occurrence['end']
        if (type(a)is not int or type(b)is not int or not 0<=a<b<=len(text)
                or text[a:b]!=occurrence['text']or occurrence['original_sha256']!=source_sha256):raise ValueError('Resource occurrence differs')
        tool=envelope.get('tool_evidence')
        if tool:
            query=tool.get('captures',[{}])[0].get('response',{}).get('query',{}).get('text')
            if query!=occurrence['text']or envelope.get('query')!=occurrence['text']:raise ValueError('Lexical query occurrence differs')
        reference=envelope.get('reference_response')
        if reference and(reference.get('question')!=occurrence['text']or envelope.get('query')!=occurrence['text']):raise ValueError('Reference query occurrence differs')
        if reference and envelope.get('source_passages',[])!=reference.get('passages',[]):raise ValueError('Native reader response differs')
        for bound in envelope.get('all_occurrences')or[occurrence]:
            if (type(bound.get('start'))is not int or type(bound.get('end'))is not int or not 0<=bound['start']<bound['end']<=len(text)or text[bound['start']:bound['end']]!=bound.get('text')or bound.get('original_sha256')!=source_sha256):raise ValueError('Repeated resource occurrence differs')
        for passage in envelope.get('source_passages',[])+envelope.get('tool_passages',[]):
            eid=passage.get('passage_id');body=passage.get('text')
            if (not isinstance(eid,str)or not eid or not isinstance(body,str)or not body
                    or len(body)>65536 or not isinstance(passage.get('source_slug'),str)
                    or not isinstance(passage.get('source_revision'),str)):raise ValueError('Native context passage differs')
            excluded=native_exclusion(passage)
            raw={k:deepcopy(passage[k])for k in ('title','url','source_revision','source_status','edition','date','publication_scope')if k in passage}
            raw.update(original_text=body,source_unit=deepcopy(passage),language=envelope['language'],
                coverage_note=passage.get('tool_scope')or'Qualified retained source context; relevance is not applicability or correction authority.')
            row={'evidence_id':eid,'capture_kind':'ordinary_context_unit','capture':raw,'capture_sha256':digest(raw),
                'source_slug':passage['source_slug'],'external_id':passage.get('source_id')or eid,
                'rights_mode':passage.get('rights_mode')if isinstance(passage.get('rights_mode'),str)else'restricted',
                'context_eligible':passage.get('context_eligible',True)and excluded is None,
                'source_role':passage.get('role'),'tool_kind':passage.get('tool_kind'),
                'occurrences':deepcopy(envelope.get('all_occurrences')or[occurrence]),'native_passage':deepcopy(passage),
                'model_context_eligible':excluded is None and(passage.get('context_eligible',True)or passage.get('role')=='tool_result'and passage.get('tool_kind')in {'synonyms','collocations','morphology'}),
                'context_use':'candidate_context_only_not_sentence_applicability'}
            nomination=('ordinary_source_owned_lexical_candidate'if passage.get('role')=='tool_result'else reference_nomination(passage,occurrence['text']))
            row['document_admission']={'role':'candidate_context'if nomination and excluded is None else'retained_lookup_only',
                'basis':nomination or'no_supported_source_topic_nomination','reason':excluded,'applicability_not_established':True}
            question=native_question(passage)
            reader_text=(('Izvirno vprašanje:\n'+question+'\n\n')if isinstance(question,str)else'')+body
            row['source']={'source_id':passage.get('source_id')or eid,'source_revision':passage['source_revision'],
                'evidence_id':eid,'title':passage.get('title')or row['external_id'],
                'source_label':passage.get('source_label')or passage['source_slug'],'url':passage.get('url'),
                'excerpt':reader_text,'reader_blocks':[{'heading':passage.get('source_label')or passage['source_slug'],'text':reader_text}],
                'locator':deepcopy(passage.get('span')or{}),
                'bibliography':{k:deepcopy(passage[k])for k in ('source_id','source_revision','authors','date','edition','source_status','publication_scope','tool_scope','editorial_status','tool_provenance','context')if k in passage},
                'native_source_unit':deepcopy(passage)}
            if excluded:
                row['source']['reader_blocks'].insert(0,{'heading':'Omejitev zajetega gradiva',
                    'text':'Gradivo je nepopolno ali omejeno in se ne uporablja kot vsebinsko priporočilo.'})
                row['source']['bibliography']['context_exclusion']=excluded
            siblings=[p for p in envelope.get('source_passages',[])if (p.get('source_id'),p.get('source_revision'),p.get('role'),(p.get('span')or{}).get('post_id'))==(passage.get('source_id'),passage.get('source_revision'),passage.get('role'),(passage.get('span')or{}).get('post_id'))]
            complete_proof=bool(reference and reference.get('contract')=='klikai.reference-source-context.v1'
                and reference.get('source_only')is True and passage in reference.get('passages',[])
                and siblings and all(native_exclusion(p)is None and p.get('context_eligible',True)for p in siblings))
            binding={'envelope_index':envelope_index,'passage_sha256':digest(passage),
                'occurrences':deepcopy(envelope.get('all_occurrences')or[occurrence]),
                'reference_response_sha256':digest(reference)if reference else None,
                'source_group':{k:passage.get(k)for k in ('source_id','source_revision','role')},
                'post_id':(passage.get('span')or{}).get('post_id'),
                'native_siblings':[{'passage_id':p['passage_id'],'passage_sha256':digest(p),'span':deepcopy(p.get('span'))}for p in siblings],
                'reader_complete_group':complete_proof,'native_context_eligible':passage.get('context_eligible',True),
                'native_exclusion':excluded,'nomination':nomination}
            if eid in found:
                previous=found[eid]
                # Ranking and group eligibility belong to the lookup. Every
                # original scored/qualified variant is retained unchanged.
                identity=lambda p:{k:v for k,v in p.items()if k not in {'context_eligible','warnings','fusion_score','lexical_bm25','rerank_score','rights_mode','incomplete','missing_content','missing_fields','content_status'}}
                if identity(previous['native_passage'])!=identity(passage):raise ValueError('Conflicting native source identity')
                if passage not in previous['native_passage_variants']:previous['native_passage_variants'].append(deepcopy(passage))
                previous['native_bindings'].append(binding)
                for bound in row['occurrences']:
                    if bound not in previous['occurrences']:previous['occurrences'].append(deepcopy(bound))
            else:
                row.update(native_passage_variants=[deepcopy(passage)],native_bindings=[binding])
                found[eid]=row
    for row in found.values():
        variants=sorted(row['native_passage_variants'],key=digest)
        row['native_passage_variants']=variants
        by_hash={digest(p):p for p in variants}
        def hard_exclusion(p):
            p=deepcopy(p);p['warnings']=[w for w in p.get('warnings',[])if w!='incomplete_answer_group']
            return native_exclusion(p)
        hard=next((hard_exclusion(p)for p in variants if hard_exclusion(p)),None)
        local_partial=any('incomplete_answer_group'in p.get('warnings',[])for p in variants)
        admitted=[]
        for binding in row['native_bindings']:
            p=by_hash[binding['passage_sha256']]
            lexical=p.get('role')=='tool_result'and p.get('tool_kind')in {'synonyms','collocations','morphology'}
            allowed=not hard and not binding['native_exclusion']and(p.get('context_eligible',True)or lexical)
            if local_partial:allowed=allowed and binding['reader_complete_group']
            binding['admitted_candidate_context']=bool(allowed and binding['nomination'])
            if allowed:admitted.append(binding)
        chosen=by_hash[(admitted or row['native_bindings'])[0]['passage_sha256']]
        row['native_passage']=deepcopy(chosen)
        row['capture']['source_unit']=deepcopy(chosen)
        if len(variants)>1:row['capture']['source_unit_variants']=deepcopy(variants)
        row['capture_sha256']=digest(row['capture'])
        row['context_eligible']=any(by_hash[b['passage_sha256']].get('context_eligible',True)for b in admitted)
        row['model_context_eligible']=bool(admitted)
        row['admitted_occurrences']=[]
        for binding in admitted:
            if binding['nomination']:
                for occurrence in binding['occurrences']:
                    if occurrence not in row['admitted_occurrences']:row['admitted_occurrences'].append(deepcopy(occurrence))
        row['document_admission']={'role':'candidate_context'if row['admitted_occurrences']else'retained_lookup_only',
            'basis':next((b['nomination']for b in admitted if b['nomination']),'no_supported_source_topic_nomination'),
            'reason':hard or ('native_content_incomplete_or_missing'if not admitted and local_partial else None),
            'applicability_not_established':True}
        question=native_question(chosen)
        body=(('Izvirno vprašanje:\n'+question+'\n\n')if isinstance(question,str)else'')+chosen['text']
        row['source']['excerpt']=body
        row['source']['reader_blocks']=[{'heading':chosen.get('source_label')or chosen['source_slug'],'text':body}]
        row['source']['native_source_unit']=deepcopy(chosen)
        row['source']['native_source_unit_variants']=deepcopy(variants)
        row['source']['bibliography'].pop('context_exclusion',None)
        row['source']['bibliography']['native_context_bindings']=deepcopy(row['native_bindings'])
        if hard or not admitted and local_partial:
            row['source']['bibliography']['context_exclusion']=row['document_admission']['reason']
            row['source']['reader_blocks'].insert(0,{'heading':'Omejitev zajetega gradiva','text':'Gradivo je nepopolno ali omejeno in se ne uporablja kot vsebinsko priporočilo.'})
        elif local_partial:
            row['source']['reader_blocks'].insert(0,{'heading':'Omejitev posamezne poizvedbe','text':'Ena poizvedba je vrnila le del skupine. Za navedeni izvirni odsek se uporablja ločeno zajeta popolna skupina; sled delne poizvedbe ostaja shranjena.'})
        group=frozen_reader_group(row,value)
        if group is not None:row['source']['frozen_native_group']=group
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
