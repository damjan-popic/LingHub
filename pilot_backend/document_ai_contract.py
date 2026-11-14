"""Pure mechanical AI-suggestion contract, identical in CORE and WEB.

Mechanical eligibility never certifies meaning, grammar or source authority.
"""
from copy import deepcopy
from collections import Counter
import hashlib,json,re
from .document_protection import protected
VERSION='klikai.document-ai-suggestions.v1'
PACKET='klikai.document-ai-packet.v1'
EVIDENCE_LABEL='Predlogi na podlagi virov KLIKai'
AI_LABEL='Predlogi umetne inteligence — preverite pred uporabo'
WARNING='Predlogi umetne inteligence lahko vsebujejo napake ali spremenijo pomen. Pred uporabo jih preverite. Predlogi niso samodejno vključeni v besedilo.'
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def sha(text):return hashlib.sha256(text.encode()).hexdigest()
def checked_packet(packet):
    if (not isinstance(packet,dict)or packet.get('version')!=PACKET
            or packet.get('packet_sha256')!=digest({k:v for k,v in packet.items()if k!='packet_sha256'})
            or packet.get('locale')not in {'sl','it'}or not re.fullmatch('[0-9a-f]{64}',str(packet.get('evidence_manifest_sha256')))
            or not re.fullmatch('[0-9a-f]{64}',str(packet.get('document_revision')))
            or not isinstance(packet.get('sections'),list)or not 1<=len(packet['sections'])<=100
            or not isinstance(packet.get('sources'),dict)):
        raise ValueError('AI packet identity differs')
    ids=set()
    for section in packet['sections']:
        if (type(section.get('index'))is not int or section['index']in ids
                or section.get('original_sha256')!=sha(section['original_text'])
                or section.get('draft_sha256')!=sha(section['evidence_draft'])):
            raise ValueError('AI section binding differs')
        ids.add(section['index']);rendered=[];last=0
        for edit in section['evidence_edits']:
            a,b=edit['start'],edit['end']
            if not 0<=last<=a<=b<=len(section['original_text'])or section['original_text'][a:b]!=edit['original']:
                raise ValueError('Evidence edit map differs')
            rendered.extend([section['original_text'][last:a],edit['replacement']]);last=b
        if ''.join(rendered)+section['original_text'][last:]!=section['evidence_draft']:raise ValueError('Evidence draft differs')
    for key,source in packet['sources'].items():
        if (not re.fullmatch('S[0-9a-f]{16}',key)or source.get('body_sha256')!=sha(source['original_text'])):
            raise ValueError('Qualified source differs')
    if packet.get('coverage',{}).get('provided_sections')!=[s['index']for s in packet['sections']]:raise ValueError('Coverage differs')
    return packet

def overlap(a,b,c,d):return max(a,c)<min(b,d)or a==b and c<=a<=d or c==d and a<=c<=b

def original_span(section,start,end):
    """Map an unchanged evidence-draft range exactly through recorded edits.

    A proposal crossing an applied source change remains a comment until its
    conflict is resolved; it cannot independently reverse an evidence decision.
    """
    shift=0
    for edit in section['evidence_edits']:
        a=edit['start']+shift;b=a+len(edit['replacement'])
        if max(start,a)<min(end,b)or a==b and start<a<end:raise ValueError('overlaps_evidence_change')
        shift+=len(edit['replacement'])-(edit['end']-edit['start'])
    def point(value,side):
        shift=0
        for edit in section['evidence_edits']:
            a=edit['start']+shift;b=a+len(edit['replacement'])
            if value<a:return value-shift
            if value==a:
                if a==b and side=='start':
                    shift+=len(edit['replacement'])-(edit['end']-edit['start']);continue
                return edit['start']
            if value<b:raise ValueError('overlaps_evidence_change')
            if value==b and side=='end':return edit['end']
            shift+=len(edit['replacement'])-(edit['end']-edit['start'])
        return value-shift
    return point(start,'start'),point(end,'end')

def checked_output(raw,packet):
    checked_packet(packet)
    if not isinstance(raw,dict)or set(raw)!={'items'}or not isinstance(raw['items'],list)or len(raw['items'])>24:
        raise ValueError('AI output shape differs')
    sections={s['index']:s for s in packet['sections']};known_findings={f['finding_id']for s in packet['sections']for f in s['findings']}
    result=[]
    fields={'kind','section_index','start','end','observed_text','replacement','explanation','uncertainty','finding_ids','source_refs'}
    for position,row in enumerate(raw['items']):
        if (not isinstance(row,dict)or set(row)!=fields or row['kind']not in {'finding_comment','additional_suggestion'}
                or type(row['section_index'])is not int or type(row['start'])is not int or type(row['end'])is not int
                or not isinstance(row['observed_text'],str)or len(row['observed_text'])>3000
                or row['replacement']is not None and(not isinstance(row['replacement'],str)or len(row['replacement'])>3000)
                or not isinstance(row['explanation'],str)or not 1<=len(row['explanation'].strip())<=2000
                or not isinstance(row['uncertainty'],str)or len(row['uncertainty'])>1000
                or not all(isinstance(row[k],list)and len(row[k])<=64 and all(isinstance(v,str)for v in row[k])for k in ('finding_ids','source_refs'))
                or any(marker in row['explanation']for marker in ('<think>','</think>','|channel|'))):
            raise ValueError('Invalid AI item')
        flags=[];section=sections.get(row['section_index']);mapped=None
        if section is None:flags.append('unknown_section')
        elif (not 0<=row['start']<row['end']<=len(section['evidence_draft'])
                or section['evidence_draft'][row['start']:row['end']]!=row['observed_text']):flags.append('span_not_bound')
        else:
            if row['replacement']is not None:
                if row['replacement']==row['observed_text']:flags.append('no_op')
                replacement=row['replacement']
                if any(ord(c)<32 and c not in '\n\t\r' or 0xD800<=ord(c)<=0xDFFF for c in replacement):flags.append('invalid_document_character')
                before=section['evidence_draft'];after=before[:row['start']]+replacement+before[row['end']:]
                values=r'https?://[^\s<>"«»]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\{[^{}\n]+\}|\b[A-Za-z]{1,12}[-.:]\d[\w.:-]*\b|\d+(?:[.,:/-]\d+)*'
                if Counter(re.findall(values,before))!=Counter(re.findall(values,after)):flags.append('protected_values_changed')
                reason=protected(section['evidence_draft'],row['start'],row['end'])
                if reason:flags.append(reason)
                # Full identifiers/numbers are protected too; the source-form
                # boundary helper also guards matches inside connected IDs.
                for match in re.finditer(r'\b[A-Za-z]{1,12}[-.:]\d[\w.:-]*\b|\b\d+(?:[.,:/-]\d+)*\b',section['evidence_draft']):
                    if max(row['start'],match.start())<min(row['end'],match.end()):flags.append('protected_identifier_or_number');break
                try:
                    mapped=original_span(section,row['start'],row['end'])
                    if section['original_text'][mapped[0]:mapped[1]]!=row['observed_text']:flags.append('original_mapping_not_bound')
                except ValueError as error:flags.append(str(error))
        if not set(row['finding_ids'])<=known_findings or not set(row['source_refs'])<=set(packet['sources']):flags.append('unknown_reference')
        if row['kind']=='finding_comment'and not row['finding_ids']:flags.append('finding_comment_without_reference')
        item={**deepcopy(row),'suggestion_id':'A'+digest({'packet':packet['packet_sha256'],'position':position,'item':row})[:24],
            'origin':'ai_generated','authority':'not_source_authorized','draft_sha256':section['draft_sha256']if section else None,
            'mechanical_issues':list(dict.fromkeys(flags)),'eligible_for_accept':row['replacement']is not None and not flags,
            'original_span':{'start':mapped[0],'end':mapped[1]}if mapped is not None else None}
        result.append(item)
    return result

def selected_sections(packet,items,decisions):
    checked_packet(packet);by_section={s['index']:[]for s in packet['sections']}
    for item in items:
        if decisions.get(item['suggestion_id'],{}).get('action')!='accept':continue
        if not item['eligible_for_accept']:raise ValueError('Suggestion is not mechanically eligible')
        by_section[item['section_index']].append(item)
    selected=[]
    for section in packet['sections']:
        chosen=sorted(by_section[section['index']],key=lambda x:(x['start'],x['end']))
        for i,item in enumerate(chosen):
            if any(overlap(item['start'],item['end'],other['start'],other['end'])for other in chosen[:i]):raise ValueError('overlapping_suggestions')
        edits=deepcopy(section['evidence_edits'])
        for item in chosen:
            a,b=item['original_span']['start'],item['original_span']['end']
            if section['original_text'][a:b]!=item['observed_text']:raise ValueError('Original mapping differs')
            edits.append({'start':a,'end':b,'original':item['observed_text'],'replacement':item['replacement'],
                          'explanation':item['explanation'],'ai_suggestion_id':item['suggestion_id']})
        if len(edits)>64:raise ValueError('selected_edit_limit')
        edits.sort(key=lambda x:(x['start'],x['end']));parts=[];last=0
        for edit in edits:
            if edit['start']<last:raise ValueError('overlapping_original_edits')
            parts.extend([section['original_text'][last:edit['start']],edit['replacement']]);last=edit['end']
        final=''.join(parts)+section['original_text'][last:]
        # Independently reconstruct through the frozen draft coordinate base.
        expected=section['evidence_draft']
        for item in reversed(chosen):expected=expected[:item['start']]+item['replacement']+expected[item['end']:]
        if final!=expected:raise ValueError('Selected reconstruction differs')
        selected.append({'index':section['index'],'location':section['location'],'text':section['original_text'],'state':'complete',
            'document_result':{'status':'draft','mode':'review','source_sha256':section['original_sha256'],'proposed_text':final,'edits':edits}})
    return selected
